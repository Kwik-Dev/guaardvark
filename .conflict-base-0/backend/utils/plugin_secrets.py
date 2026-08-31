"""Plugin secret fields that live in `.env`, never in plugin.json.

Discord (and future plugins) need credentials that must not ride source sync.
This module maps UI field names → allowlisted PORTABLE_ENV_KEYS and exposes
masked status for the Plugins config dialog.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, MutableMapping, Optional

logger = logging.getLogger(__name__)

# plugin_id → { ui_field_name: ENV_VAR }
PLUGIN_SECRET_FIELDS: Dict[str, Dict[str, str]] = {
    "discord": {
        "bot_token": "DISCORD_BOT_TOKEN",
    },
}


def secret_field_names(plugin_id: str) -> frozenset:
    """Return UI field names that are secrets for this plugin."""
    return frozenset(PLUGIN_SECRET_FIELDS.get(plugin_id, {}).keys())


def is_masked_or_empty(value: Any) -> bool:
    """True when *value* carries no new secret — blank, or a mask echoed back.

    Callers use this to implement "leave blank to keep the existing token".
    """
    if value is None:
        return True
    if not isinstance(value, str):
        return True
    stripped = value.strip()
    if not stripped:
        return True
    # Placeholder / hint pasted back from the UI (••••abcd or ****abcd)
    if all(c in "•*" for c in stripped):
        return True
    if stripped.startswith(("•", "*")) and len(stripped) <= 16:
        # e.g. "••••abcd" — treat as non-update
        body = stripped.lstrip("•*")
        if len(body) <= 4 and body.isalnum():
            return True
    return False


def mask_hint(value: str) -> str:
    """Render a secret as ``••••`` plus its last four characters."""
    if len(value) <= 4:
        return "••••"
    return f"••••{value[-4:]}"


def _read_env_value(env_key: str, file_sync_service=None) -> str:
    """Prefer live process env; fall back to allowlisted `.env` read."""
    live = os.environ.get(env_key, "").strip()
    if live:
        return live
    try:
        if file_sync_service is None:
            from backend.services.interconnector_file_sync_service import (
                get_file_sync_service,
            )
            file_sync_service = get_file_sync_service()
        creds = file_sync_service.read_portable_env_credentials()
        return (creds.get(env_key) or "").strip()
    except Exception as e:
        logger.warning("Failed reading %s from portable env: %s", env_key, e)
        return ""


def get_secret_status(
    plugin_id: str,
    *,
    file_sync_service=None,
) -> Dict[str, Dict[str, Any]]:
    """Return masked status for each secret field (never the full value)."""
    mapping = PLUGIN_SECRET_FIELDS.get(plugin_id)
    if not mapping:
        return {}

    status: Dict[str, Dict[str, Any]] = {}
    for field, env_key in mapping.items():
        value = _read_env_value(env_key, file_sync_service=file_sync_service)
        if value:
            status[field] = {
                "configured": True,
                "hint": mask_hint(value),
                "env_key": env_key,
            }
        else:
            status[field] = {
                "configured": False,
                "hint": "",
                "env_key": env_key,
            }
    return status


def apply_secret_updates(
    plugin_id: str,
    payload: MutableMapping[str, Any],
    *,
    file_sync_service=None,
) -> Dict[str, Any]:
    """Pop secret fields from *payload* and write real values to `.env`.

    Mutates *payload* in place so callers can safely pass the remainder to
    ``update_plugin_config`` (manifest). Empty / masked values are discarded
    without clearing the existing token.

    Returns:
        {
          "updated": [field, ...],   # fields actually written
          "skipped_empty": bool,     # had secret keys but all empty/masked
          "env_result": {...} | None,
        }
    """
    mapping = PLUGIN_SECRET_FIELDS.get(plugin_id, {})
    result: Dict[str, Any] = {
        "updated": [],
        "skipped_empty": False,
        "env_result": None,
    }
    if not mapping:
        return result

    to_write: Dict[str, str] = {}
    saw_secret_key = False

    for field, env_key in mapping.items():
        if field not in payload:
            continue
        saw_secret_key = True
        raw = payload.pop(field)
        if is_masked_or_empty(raw):
            continue
        to_write[env_key] = raw.strip()

    if saw_secret_key and not to_write:
        result["skipped_empty"] = True
        return result

    if not to_write:
        return result

    try:
        if file_sync_service is None:
            from backend.services.interconnector_file_sync_service import (
                get_file_sync_service,
            )
            file_sync_service = get_file_sync_service()
        env_result = file_sync_service.merge_portable_env_credentials(to_write)
        result["env_result"] = env_result
        if env_result.get("error"):
            logger.error(
                "Failed writing plugin secrets for %s: %s",
                plugin_id,
                env_result["error"],
            )
            return result
    except Exception as e:
        logger.error("Failed writing plugin secrets for %s: %s", plugin_id, e)
        result["env_result"] = {"error": str(e)}
        return result

    # Keep current process in sync so child restarts that inherit env see it.
    for env_key, value in to_write.items():
        os.environ[env_key] = value
        # Map back to UI field name for the caller.
        for field, key in mapping.items():
            if key == env_key:
                result["updated"].append(field)
                break

    logger.info(
        "Updated plugin secrets for %s: %s",
        plugin_id,
        result["updated"],
    )
    return result


def enrich_with_secrets(
    plugin_id: str,
    info: Optional[Dict[str, Any]],
    *,
    file_sync_service=None,
) -> Dict[str, Any]:
    """Attach a ``secrets`` status block to a plugin info/config dict."""
    if info is None:
        info = {}
    status = get_secret_status(plugin_id, file_sync_service=file_sync_service)
    if status:
        info["secrets"] = status
    return info
