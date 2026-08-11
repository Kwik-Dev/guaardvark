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


def _clamp_params(params: dict) -> dict:
    clamped = {}
    for key, value in params.items():
        if key in _BOOL_PARAMS:
            clamped[key] = bool(value)
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
