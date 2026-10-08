"""RAG parameter resolution: experiment overrides + promoted active config.

Two layers feed retrieval (backend/services/indexing_service.py):

1. EXPERIMENT override — set by the autoresearch eval harness around a single
   eval call via set_experiment_config(). ContextVar-based, so it survives
   async/greenlet hops within the calling context and never leaks across
   requests. Outside an eval this is None.

2. ACTIVE PROMOTED config — the winning parameter set autoresearch promoted
   (ResearchConfig row with is_active=True). Cached with a short TTL;
   promotion/revert calls invalidate_active_params_cache() for immediacy.

get_active_rag_params() merges the two (experiment wins) into an OVERLAY dict
and clamps every value. An empty overlay — no promotion, no experiment — means
"legacy behavior": callers keep their existing defaults (env-driven alpha,
env-gated rerank, model-aware dedup threshold), so a box that never promoted
anything behaves exactly as before this layer existed.
"""
import logging
import time
from contextvars import ContextVar
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_experiment_config: ContextVar[Optional[dict]] = ContextVar(
    "rag_experiment_config", default=None
)

# Hard bounds, enforced on BOTH promoted configs and experiment overrides.
# Defense in depth: a corrupt/hostile ResearchConfig row must not be able to
# set top_k=500 and melt retrieval. Mirrors PARAM_RANGES in
# backend/services/rag_experiment_agent.py.
_PARAM_CLAMPS = {
    "top_k": (1, 20, int),
    "dedup_threshold": (0.5, 0.98, float),
    "context_window_chunks": (1, 10, int),
    "hybrid_search_alpha": (0.0, 1.0, float),
    "chunk_size": (200, 3000, int),
    "chunk_overlap": (0, 500, int),
}
_BOOL_PARAMS = {
    "reranking_enabled", "query_expansion", "use_semantic_splitting",
    "use_hierarchical_splitting", "extract_entities", "preserve_structure",
}

_ACTIVE_CACHE_TTL_S = 60.0
_active_cache: Dict[str, Any] = {"params": None, "loaded_at": 0.0}


def set_experiment_config(config: dict):
    """Set experiment params for the current context (one eval call)."""
    _experiment_config.set(config)


def get_experiment_config() -> Optional[dict]:
    """Get experiment params, or None if not inside an experiment."""
    return _experiment_config.get()


def clear_experiment_config():
    """Remove experiment config from the current context."""
    _experiment_config.set(None)


def invalidate_active_params_cache():
    """Force the next get_active_rag_params() to reload the promoted config.

    Called on promotion, activation, and revert so changes apply immediately
    instead of after the TTL.
    """
    _active_cache["params"] = None
    _active_cache["loaded_at"] = 0.0


_TRUE_WORDS = {"true", "1", "yes"}
_FALSE_WORDS = {"false", "0", "no"}


def parse_bool(value) -> Optional[bool]:
    """True or False for a recognised boolean, else None.

    Accepts bools, the integers 1 and 0, and the words true/false, 1/0 and
    yes/no in any case. bool() is not used: bool('false') is True.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    return None


def _clamp_params(params: dict) -> dict:
    clamped = {}
    for key, value in params.items():
        if key in _BOOL_PARAMS:
            flag = parse_bool(value)
            if flag is None:
                logger.warning("Dropping non-boolean RAG param %s=%r", key, value)
                continue
            clamped[key] = flag
            continue
        bounds = _PARAM_CLAMPS.get(key)
        if bounds is None:
            clamped[key] = value
            continue
        low, high, cast = bounds
        try:
            v = cast(value)
        except (TypeError, ValueError):
            logger.warning("Dropping non-numeric RAG param %s=%r", key, value)
            continue
        if v < low or v > high:
            logger.warning(
                "Clamping RAG param %s=%s to [%s, %s]", key, v, low, high
            )
            v = min(max(v, low), high)
        clamped[key] = v
    return clamped


def normalise_param(key: str, value):
    """`value` as retrieval reads it (cast, clamped, strict booleans), or None
    when it is not usable for `key`."""
    return _clamp_params({key: value}).get(key)


def changed_params(params: dict, baseline: dict) -> dict:
    """The entries of `params` whose value differs from `baseline`, both clamped.

    A promoted config must carry only what experiments changed: every key it
    holds overrides retrieval's own default, including defaults that are
    resolved per embedding model, so a copied default would go live with it.
    """
    mine = _clamp_params(params or {})
    base = _clamp_params(baseline or {})
    return {k: v for k, v in mine.items() if k not in base or base[k] != v}


def _load_promoted_params() -> Optional[dict]:
    """Read the active ResearchConfig row. Fail-soft: any error → None.

    Needs an app context; callers outside one (rare — retrieval always runs
    inside a request or a pushed context) just get legacy behavior.
    """
    try:
        from backend.models import ResearchConfig
        row = (
            ResearchConfig.query.filter_by(is_active=True)
            .order_by(ResearchConfig.promoted_at.desc())
            .first()
        )
        if row and isinstance(row.params, dict) and row.params:
            return dict(row.params)
    except Exception as e:
        logger.debug(f"Active RAG config unavailable: {e}")
    return None


def get_active_rag_params() -> dict:
    """Overlay of promoted-config params + experiment overrides, clamped.

    Empty dict = no promotion and no experiment: callers use their legacy
    defaults. Experiment values win over promoted values.
    """
    now = time.time()
    if now - _active_cache["loaded_at"] > _ACTIVE_CACHE_TTL_S:
        _active_cache["params"] = _load_promoted_params()
        _active_cache["loaded_at"] = now

    overlay: Dict[str, Any] = {}
    promoted = _active_cache["params"]
    if promoted:
        overlay.update(promoted)
    exp = _experiment_config.get()
    if exp:
        overlay.update(exp)
    if not overlay:
        return {}
    return _clamp_params(overlay)
