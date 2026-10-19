"""One capability record per Ollama model tag.

Built from Ollama's own description of the model (``/api/show``, through
``ollama_resource_manager.get_model_info`` and its cache), then any declared
row for the tag (``model_capability_data.MODEL_CAPABILITY_ROWS`` and, on this
machine, ``data/config/model_capabilities.json``). Vision is the capability
resolver's answer (``model_capability_resolver.sees_natively``), which has its
own authority order.

Where a name rule decides something Ollama also reports, the record keeps both
answers side by side instead of choosing: ``thinking`` is what Ollama says,
``thinks_by_name`` is the THINKING_NAME_PATTERNS rule, and the helpers that
send ``think:false`` still use either, as they did before. Each field names
where it came from in ``evidence``.

Importable from Flask, Celery and the MCP server alike: no app context.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from backend.services.model_capability_data import (
    DECISION_NAME_PATTERNS,
    INLINE_REASONING_TAGS,
    MODEL_CAPABILITY_ROWS,
    OVERRIDABLE_FIELDS,
    THINKING_NAME_EXCLUDE_PATTERNS,
    THINKING_NAME_PATTERNS,
)

logger = logging.getLogger(__name__)

LOCAL_ROWS_PATH = Path(__file__).resolve().parents[2] / "data" / "config" / "model_capabilities.json"

_local_lock = threading.Lock()
_local_cache: Dict[str, Any] = {"mtime": None, "rows": {}}


@dataclass(frozen=True)
class ModelRecord:
    tag: str
    exists: bool                  # Ollama described the model
    capabilities: Tuple[str, ...]  # /api/show "capabilities", as reported
    completion: bool
    tools: bool
    thinking: bool                # Ollama lists "thinking"
    thinks_by_name: bool          # THINKING_NAME_PATTERNS matches the tag
    reasoning_tags: Tuple[Tuple[str, str], ...]  # tag pairs around inline reasoning
    vision: bool
    embedding: bool
    native_context: int           # <arch>.context_length (0 when unknown)
    embedding_dim: int            # <arch>.embedding_length (0 when unknown)
    parameter_count: int
    size_mb: float
    architecture: str
    evidence: Dict[str, str] = field(default_factory=dict)
    decision: bool = False        # Ollama lists "decision": a label-only model, no chat reply

    @property
    def sends_think_flag(self) -> bool:
        """Whether think:false is sent to this model (model_supports_thinking)."""
        return self.thinks_by_name or self.thinking


def thinks_by_name(tag: Optional[str]) -> bool:
    lower = (tag or "").lower()
    return (
        bool(lower)
        and any(re.search(p, lower) for p in THINKING_NAME_PATTERNS)
        and not any(re.search(p, lower) for p in THINKING_NAME_EXCLUDE_PATTERNS)
    )


def decides_by_name(tag: Optional[str]) -> bool:
    """DECISION_NAME_PATTERNS matches the tag: the rule for when Ollama cannot
    describe the model."""
    lower = (tag or "").lower()
    return bool(lower) and any(re.search(p, lower) for p in DECISION_NAME_PATTERNS)


def _tag_pairs(value: Any) -> Tuple[Tuple[str, str], ...]:
    """[[open, close], ...] from a row, or () when it is not that shape."""
    try:
        pairs = tuple((str(o), str(c)) for o, c in value if o and c)
    except (TypeError, ValueError):
        return ()
    return pairs


def _local_rows() -> Dict[str, Dict[str, Any]]:
    """data/config/model_capabilities.json, re-read when it changes."""
    try:
        mtime = LOCAL_ROWS_PATH.stat().st_mtime
    except OSError:
        return {}
    with _local_lock:
        if _local_cache["mtime"] == mtime:
            return _local_cache["rows"]
        try:
            rows = json.loads(LOCAL_ROWS_PATH.read_text(encoding="utf-8"))
            if not isinstance(rows, dict):
                raise ValueError("expected an object keyed by model tag")
        except Exception as e:  # noqa: BLE001 - a broken file must not break chat
            logger.warning("Ignoring %s: %s", LOCAL_ROWS_PATH, e)
            rows = {}
        _local_cache.update(mtime=mtime, rows=rows)
        return rows


def declared_row(tag: str) -> Tuple[Dict[str, Any], str]:
    """The declared fields for ``tag`` and where they came from."""
    local = _local_rows().get(tag)
    if isinstance(local, dict):
        return {k: v for k, v in local.items() if k in OVERRIDABLE_FIELDS}, "local_row"
    shipped = MODEL_CAPABILITY_ROWS.get(tag)
    if isinstance(shipped, dict):
        return {k: v for k, v in shipped.items() if k in OVERRIDABLE_FIELDS}, "declared_row"
    return {}, ""


def _info(tag: str) -> Optional[dict]:
    try:
        from backend.utils.ollama_resource_manager import get_model_info
        return get_model_info(tag)
    except Exception as e:  # noqa: BLE001
        logger.debug("model info lookup failed for %r: %s", tag, e)
        return None


def _vision(tag: str) -> Tuple[bool, str]:
    try:
        from backend.services.model_capability_resolver import _vision_with_evidence
        return _vision_with_evidence(tag)
    except Exception as e:  # noqa: BLE001
        logger.debug("vision lookup failed for %r: %s", tag, e)
        return False, "unavailable"


def capabilities_for(tag: Optional[str], *, with_vision: bool = True) -> ModelRecord:
    """The record for ``tag``. Cheap to call: /api/show is cached by
    get_model_info (5 minutes; 15 s for a model Ollama could not describe)."""
    tag = tag or ""
    return record_from_info(tag, _info(tag) if tag else None, with_vision=with_vision)


def record_from_info(tag: str, info: Optional[dict], *, with_vision: bool = True) -> ModelRecord:
    """The record for ``tag`` from a get_model_info result the caller already has."""
    caps = tuple(str(c) for c in ((info or {}).get("capabilities") or []))
    source = "api_show" if info is not None else "ollama_unreachable"
    values: Dict[str, Any] = {
        "completion": "completion" in caps,
        "tools": "tools" in caps,
        "thinking": "thinking" in caps,
        "embedding": "embedding" in caps,
        "decision": "decision" in caps,
        "native_context": int((info or {}).get("native_context") or 0),
        "embedding_dim": int((info or {}).get("embedding_length") or 0),
    }
    evidence = {k: source for k in values}
    values["reasoning_tags"] = INLINE_REASONING_TAGS
    evidence["reasoning_tags"] = "default"
    row, row_source = declared_row(tag)
    for key, value in row.items():
        if key == "reasoning_tags":
            value = _tag_pairs(value)
            if not value:
                logger.warning("Ignoring reasoning_tags for %r: expected [[open, close], ...]", tag)
                continue
        values[key] = value
        evidence[key] = row_source

    by_name = thinks_by_name(tag)
    evidence["thinks_by_name"] = "name_pattern"
    if with_vision:
        vision, evidence["vision"] = _vision(tag)
    else:
        vision, evidence["vision"] = False, "not_asked"

    return ModelRecord(
        tag=tag,
        exists=info is not None,
        capabilities=caps,
        completion=bool(values["completion"]),
        tools=bool(values["tools"]),
        thinking=bool(values["thinking"]),
        thinks_by_name=by_name,
        reasoning_tags=values["reasoning_tags"],
        vision=bool(vision),
        embedding=bool(values["embedding"]),
        native_context=int(values["native_context"]),
        embedding_dim=int(values["embedding_dim"]),
        parameter_count=int((info or {}).get("parameter_count") or 0),
        size_mb=float((info or {}).get("size_mb") or 0.0),
        architecture=(info or {}).get("architecture") or "unknown",
        evidence=evidence,
        decision=bool(values["decision"]),
    )


# ── start.sh's model bootstrap ────────────────────────────────────────────
# Names that mark an embedding model, used only for a tag Ollama could not
# describe and no declared row covers (start.sh's older rule).
_EMBED_NAME_FALLBACK = re.compile(r"embed|minilm", re.IGNORECASE)


def model_kind(tag: str) -> Tuple[str, str]:
    """("embedding" | "chat", why) for an installed Ollama tag: Ollama's own
    capabilities, then a declared row's ``embedding`` (record_from_info), and
    only when neither answers, the name."""
    record = capabilities_for(tag, with_vision=False)
    declared = record.evidence.get("embedding") in ("declared_row", "local_row")
    if record.exists or declared:
        return ("embedding" if record.embedding else "chat"), record.evidence.get("embedding", "")
    return ("embedding" if _EMBED_NAME_FALLBACK.search(tag) else "chat"), "name_fallback"


def _tags_from_ollama_list(text: str) -> list:
    """Model tags from ``ollama list`` output (first column, header skipped)."""
    tags = []
    for line in (text or "").splitlines():
        first = line.split()[0] if line.split() else ""
        if first and first != "NAME" and ":" in first:
            tags.append(first)
    return tags


def main(argv=None) -> int:
    """``python -m backend.services.model_capabilities kinds`` reads
    ``ollama list`` on stdin and prints ``<tag>\t<embedding|chat>\t<why>``
    per model, for start.sh to tell chat models from embedding models."""
    import sys
    args = argv if argv is not None else sys.argv[1:]
    if args[:1] != ["kinds"]:
        print("usage: python -m backend.services.model_capabilities kinds < ollama-list-output",
              file=sys.stderr)
        return 2
    for tag in _tags_from_ollama_list(sys.stdin.read()):
        kind, why = model_kind(tag)
        print(f"{tag}\t{kind}\t{why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

