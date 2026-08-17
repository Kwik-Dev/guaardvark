"""Connection CRUD, credential binding and health probes.

Assumes an active Flask app context, like the other DB-facing services.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, MutableMapping, Optional, Tuple

from backend.services.connections import registry
from backend.services.connections.base import (
    STATUS_CONNECTED,
    STATUS_DISABLED,
    STATUS_ERROR,
    STATUS_UNCONFIGURED,
    ConnCtx,
)
from backend.utils import credential_store

logger = logging.getLogger(__name__)


def _json_loads(raw, fallback):
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return fallback


def build_ctx(connection) -> ConnCtx:
    """Assemble the provider context for *connection*, resolving its secrets."""
    from backend.models import db

    spec = registry.spec_for(connection.provider)
    ref = connection.ref
    secrets = credential_store.get_secret(ref, env_keys=spec.env_keys)

    def save_secrets(values: Dict[str, str]) -> None:
        merged = dict(credential_store.get_secret(ref))
        merged.update({k: v for k, v in values.items() if v})
        credential_store.set_secret(ref, merged, hint_field=spec.hint_field)

    def set_status(status: str, error: Optional[str]) -> None:
        connection.status = status
        connection.error_message = error
        db.session.commit()

    return ConnCtx(
        connection=connection.to_dict(env_keys=spec.env_keys),
        config=_json_loads(connection.config, {}),
        secrets=secrets,
        save_secrets=save_secrets,
        set_status=set_status,
    )


def test_connection(connection) -> Tuple[bool, str]:
    """Probe the remote service and record the outcome on the row."""
    from backend.models import db

    connection.last_test_at = datetime.now()
    try:
        module = registry.get_provider(connection.provider)
        ok, message, discovered = module.test(build_ctx(connection))
    except Exception as e:  # noqa: BLE001 - a provider must not 500 the endpoint
        logger.warning("Connection %s test raised: %s", connection.id, e, exc_info=True)
        ok, message, discovered = False, redact(str(e)), {}

    if ok:
        connection.status = STATUS_CONNECTED
        connection.error_message = None
        if discovered.get("handle"):
            connection.handle = discovered["handle"]
        if discovered.get("display_name") and not connection.display_name:
            connection.display_name = discovered["display_name"]
        if discovered.get("config"):
            merged = _json_loads(connection.config, {})
            merged.update(discovered["config"])
            connection.config = json.dumps(merged)
        if discovered.get("capabilities"):
            connection.capabilities_cache = json.dumps(discovered["capabilities"])
    else:
        connection.status = STATUS_ERROR
        connection.error_message = redact(message)

    db.session.commit()
    return ok, message


def redact(text: str) -> str:
    """Strip credential material that providers echo back in error bodies."""
    if not text:
        return ""
    cleaned = text
    for ref in credential_store.list_refs():
        for value in credential_store.get_secret(ref).values():
            if value and len(value) >= 8 and value in cleaned:
                cleaned = cleaned.replace(value, "••••")
    return cleaned


def create_connection(payload: MutableMapping[str, Any]):
    """Create a Connection, routing secret fields into the credential store."""
    from backend.models import Connection, db

    family = (payload.get("family") or "").strip()
    provider = (payload.get("provider") or "").strip()
    slug = (payload.get("account_slug") or "default").strip() or "default"
    if not provider:
        raise ValueError("A provider is required.")

    spec = registry.spec_for(provider)
    family = family or spec.family

    existing = Connection.query.filter_by(
        family=family, provider=provider, account_slug=slug
    ).first()
    if existing:
        raise ValueError(
            f"A {spec.label} connection named '{slug}' already exists."
        )

    connection = Connection(
        family=family,
        provider=provider,
        account_slug=slug,
        display_name=(payload.get("display_name") or "").strip() or None,
        auth_kind=(payload.get("auth_kind") or spec.auth_kinds[0]),
        credential_ref=credential_store.credential_ref(family, provider, slug),
        status=STATUS_UNCONFIGURED,
    )
    db.session.add(connection)
    _apply_config(connection, payload, spec)
    _apply_secrets(connection, payload, spec)
    db.session.commit()
    return connection


def update_connection(connection, payload: MutableMapping[str, Any]):
    from backend.models import db

    spec = registry.spec_for(connection.provider)
    if "display_name" in payload:
        connection.display_name = (payload.get("display_name") or "").strip() or None
    if "enabled" in payload:
        connection.enabled = bool(payload["enabled"])
        if not connection.enabled:
            connection.status = STATUS_DISABLED
        elif connection.status == STATUS_DISABLED:
            connection.status = STATUS_UNCONFIGURED

    _apply_config(connection, payload, spec)
    _apply_secrets(connection, payload, spec)
    db.session.commit()
    return connection


def _apply_config(connection, payload: MutableMapping[str, Any], spec) -> None:
    incoming = payload.get("config")
    if not isinstance(incoming, dict):
        return
    allowed = {f.name for f in spec.config_fields}
    merged = _json_loads(connection.config, {})
    for key, value in incoming.items():
        if key in allowed:
            merged[key] = value
    connection.config = json.dumps(merged)


def _apply_secrets(connection, payload: MutableMapping[str, Any], spec) -> Dict[str, Any]:
    """Move secret fields out of *payload* and into the credential store."""
    field_names = [f.name for f in spec.credential_fields]
    if not field_names:
        return {"updated": [], "skipped_empty": False}

    result = credential_store.apply_secret_updates(
        connection.ref, payload, field_names, hint_field=spec.hint_field
    )
    if result["updated"]:
        connection.credential_source = "file"
        if connection.status == STATUS_UNCONFIGURED:
            connection.error_message = None
    return result


def delete_connection(connection) -> None:
    """Remove the row and its stored secret together."""
    from backend.models import db

    ref = connection.ref
    db.session.delete(connection)
    db.session.commit()
    credential_store.delete_secret(ref)


def list_connections(family: Optional[str] = None) -> List[Dict[str, Any]]:
    from backend.models import Connection

    query = Connection.query
    if family:
        query = query.filter_by(family=family)

    out: List[Dict[str, Any]] = []
    for connection in query.order_by(Connection.family, Connection.provider).all():
        try:
            env_keys = registry.spec_for(connection.provider).env_keys
        except KeyError:
            env_keys = ()
        out.append(connection.to_dict(env_keys=env_keys))
    return out


def provider_catalog(family: Optional[str] = None) -> List[Dict[str, Any]]:
    return [spec.to_dict() for spec in registry.list_specs(family)]
