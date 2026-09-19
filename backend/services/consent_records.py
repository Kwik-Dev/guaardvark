"""Durable consent records for likeness references.

A face photo may only drive ``generate_identity`` when the person using it has
confirmed they have the right to that likeness. The confirmation is stored,
not passed: a ``.consent`` sidecar next to the reference image, the same
convention the voice-clone reference clips use (``audio_foundry_api``), plus a
content-hash copy under ``OUTPUT_DIR/consent/`` so the same photo attached
again in a later chat turn (the chat engine writes each attachment to a fresh
temp path) is recognised without asking twice.

Only the consent step writes a record: the chat approval card, or a future UI
upload route. Tools read it; MCP and CLI callers cannot substitute a flag.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SIDECAR_SUFFIX = ".consent"


def sidecar_path(image_path: str) -> str:
    """``<image>.consent``, next to the image."""
    return f"{image_path}{SIDECAR_SUFFIX}"


def _hash_dir() -> str:
    try:
        from backend.config import OUTPUT_DIR
    except Exception:
        OUTPUT_DIR = "."
    return os.path.join(OUTPUT_DIR, "consent")


def content_hash(image_path: str) -> Optional[str]:
    """SHA-256 of the file bytes; None when the file cannot be read."""
    try:
        h = hashlib.sha256()
        with open(image_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _hash_record_path(digest: str) -> str:
    return os.path.join(_hash_dir(), f"{digest}{SIDECAR_SUFFIX}")


def consent_record(image_path: str) -> Optional[Dict[str, Any]]:
    """The stored record for this image, by sidecar first, then by content hash."""
    if not image_path:
        return None
    for candidate in (sidecar_path(image_path), None):
        if candidate is None:
            digest = content_hash(image_path)
            if not digest:
                return None
            candidate = _hash_record_path(digest)
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    text = f.read().strip()
                return json.loads(text) if text else {"path": image_path}
            except (OSError, ValueError):
                # An empty or hand-made sidecar still counts as consent, as it
                # does for voice clips; only its details are unknown.
                return {"path": image_path}
    return None


def has_consent(image_path: str) -> bool:
    """True when a consent record exists for this image (sidecar or content hash)."""
    return consent_record(image_path) is not None


def record_consent(image_path: str, source: str, *, session_id: Optional[str] = None,
                   note: Optional[str] = None) -> Dict[str, Any]:
    """Write the record (who/when/how) as JSON to the sidecar and the hash copy.

    ``source`` names the step that obtained the confirmation, for example
    ``chat_approval`` or ``ui_upload``. Returns the record written.
    """
    if not image_path or not os.path.isfile(image_path):
        raise FileNotFoundError(f"reference image not found: {image_path}")
    digest = content_hash(image_path)
    record: Dict[str, Any] = {
        "path": os.path.abspath(image_path),
        "sha256": digest,
        "source": source,
        "session_id": session_id,
        "note": note,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    payload = json.dumps(record, indent=2)
    with open(sidecar_path(image_path), "w", encoding="utf-8") as f:
        f.write(payload)
    if digest:
        try:
            os.makedirs(_hash_dir(), exist_ok=True)
            with open(_hash_record_path(digest), "w", encoding="utf-8") as f:
                f.write(payload)
        except OSError as exc:
            logger.warning("consent hash record not written for %s: %s", image_path, exc)
    return record
