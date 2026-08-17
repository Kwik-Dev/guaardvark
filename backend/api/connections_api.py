"""HTTP API for outbound connections and publishing.

Auto-registered by blueprint discovery. Services are imported inside handlers,
matching the convention used across ``backend/api``.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

connections_bp = Blueprint("connections_api", __name__, url_prefix="/api/connections")


def _error(message: str, status: int = 400, **extra):
    payload = {"error": message}
    payload.update(extra)
    return jsonify(payload), status


def _get_connection(cid: int):
    from backend.models import Connection

    connection = Connection.query.get(cid)
    if connection is None:
        return None, _error("Connection not found", 404)
    return connection, None


def _get_record(pid: int):
    from backend.models import PublishRecord

    record = PublishRecord.query.get(pid)
    if record is None:
        return None, _error("Publish record not found", 404)
    return record, None


# --- providers & store -------------------------------------------------------
@connections_bp.route("/providers", methods=["GET"])
def list_providers():
    from backend.services.connections import service

    family = request.args.get("family")
    return jsonify({"providers": service.provider_catalog(family)}), 200


@connections_bp.route("/store/health", methods=["GET"])
def store_health():
    from backend.utils import credential_store

    return jsonify(credential_store.health()), 200


@connections_bp.route("/store/rotate-key", methods=["POST"])
def rotate_store_key():
    from backend.utils import credential_store

    result = credential_store.rotate_key()
    return jsonify(result), (400 if result.get("error") else 200)


@connections_bp.route("/environment", methods=["GET"])
def list_environment():
    """Credentials detected in the process environment — read-only."""
    import os

    from backend.services.connections import registry
    from backend.utils.plugin_secrets import mask_hint

    detected = []
    for spec in registry.list_specs():
        for env_key in spec.env_keys:
            value = os.environ.get(env_key, "").strip()
            if value:
                detected.append(
                    {
                        "env_key": env_key,
                        "provider": spec.provider,
                        "label": spec.label,
                        "hint": mask_hint(value),
                    }
                )
    return jsonify({"environment": detected}), 200


# --- connection CRUD ---------------------------------------------------------
@connections_bp.route("", methods=["GET"])
@connections_bp.route("/", methods=["GET"])
def list_connections_route():
    from backend.services.connections import service

    try:
        return jsonify({"connections": service.list_connections(request.args.get("family"))}), 200
    except SQLAlchemyError as e:
        from backend.models import db

        db.session.rollback()
        logger.error("Listing connections failed: %s", e)
        return _error("Database error", 500, details=str(e))


@connections_bp.route("", methods=["POST"])
@connections_bp.route("/", methods=["POST"])
def create_connection_route():
    from backend.models import db
    from backend.services.connections import service

    payload = request.get_json(silent=True) or {}
    try:
        connection = service.create_connection(dict(payload))
    except (ValueError, KeyError) as e:
        return _error(str(e), 400)
    except SQLAlchemyError as e:
        db.session.rollback()
        return _error("Database error", 500, details=str(e))
    return jsonify(connection.to_dict()), 201


@connections_bp.route("/<int:cid>", methods=["GET"])
def get_connection_route(cid):
    connection, err = _get_connection(cid)
    if err:
        return err
    return jsonify(connection.to_dict()), 200


@connections_bp.route("/<int:cid>", methods=["PUT", "PATCH"])
def update_connection_route(cid):
    from backend.models import db
    from backend.services.connections import service

    connection, err = _get_connection(cid)
    if err:
        return err
    try:
        service.update_connection(connection, dict(request.get_json(silent=True) or {}))
    except (ValueError, KeyError) as e:
        return _error(str(e), 400)
    except SQLAlchemyError as e:
        db.session.rollback()
        return _error("Database error", 500, details=str(e))
    return jsonify(connection.to_dict()), 200


@connections_bp.route("/<int:cid>", methods=["DELETE"])
def delete_connection_route(cid):
    from backend.models import db
    from backend.services.connections import service

    connection, err = _get_connection(cid)
    if err:
        return err
    try:
        service.delete_connection(connection)
    except SQLAlchemyError as e:
        db.session.rollback()
        return _error("Database error", 500, details=str(e))
    return jsonify({"deleted": True, "id": cid}), 200


@connections_bp.route("/<int:cid>/test", methods=["POST"])
def test_connection_route(cid):
    from backend.services.connections import service

    connection, err = _get_connection(cid)
    if err:
        return err
    ok, message = service.test_connection(connection)
    return jsonify({"ok": ok, "message": message, "connection": connection.to_dict()}), 200


# --- OAuth -------------------------------------------------------------------
@connections_bp.route("/<int:cid>/oauth/start", methods=["POST"])
def oauth_start(cid):
    import secrets

    from backend.services.connections import registry, service

    connection, err = _get_connection(cid)
    if err:
        return err

    payload = request.get_json(silent=True) or {}
    redirect_uri = (payload.get("redirect_uri") or "").strip()
    if not redirect_uri:
        return _error("A redirect_uri is required.", 400)

    try:
        module = registry.get_provider(connection.provider)
        if not hasattr(module, "authorize_url"):
            return _error(f"{connection.provider} does not use OAuth.", 400)
        state = secrets.token_urlsafe(24)
        url = module.authorize_url(service.build_ctx(connection), redirect_uri, state)
    except ValueError as e:
        return _error(str(e), 400)
    except Exception as e:  # noqa: BLE001
        logger.warning("OAuth start failed for %s: %s", cid, e)
        return _error(service.redact(str(e)), 500)

    return jsonify({"authorize_url": url, "state": state, "redirect_uri": redirect_uri}), 200


@connections_bp.route("/<int:cid>/oauth/complete", methods=["POST"])
def oauth_complete(cid):
    from backend.models import db
    from backend.services.connections import registry, service
    from backend.utils import credential_store

    connection, err = _get_connection(cid)
    if err:
        return err

    payload = request.get_json(silent=True) or {}
    code = (payload.get("code") or "").strip()
    redirect_uri = (payload.get("redirect_uri") or "").strip()
    if not code or not redirect_uri:
        return _error("Both code and redirect_uri are required.", 400)

    try:
        module = registry.get_provider(connection.provider)
        spec = registry.spec_for(connection.provider)
        tokens = module.exchange_code(service.build_ctx(connection), code, redirect_uri)
        merged = dict(credential_store.get_secret(connection.ref))
        merged.update({k: v for k, v in tokens.items() if v})
        credential_store.set_secret(connection.ref, merged, hint_field=spec.hint_field)
        connection.credential_source = "file"
        db.session.commit()
    except (ValueError, RuntimeError) as e:
        return _error(service.redact(str(e)), 400)
    except Exception as e:  # noqa: BLE001
        logger.warning("OAuth completion failed for %s: %s", cid, e)
        return _error(service.redact(str(e)), 500)

    ok, message = service.test_connection(connection)
    return jsonify({"ok": ok, "message": message, "connection": connection.to_dict()}), 200


# --- publishing --------------------------------------------------------------
@connections_bp.route("/publish/preflight", methods=["POST"])
def publish_preflight():
    from backend.services.connections import publish_service

    payload = request.get_json(silent=True) or {}
    result = publish_service.preflight(
        payload.get("connection_ids") or [],
        payload.get("document_ids") or [],
        body=payload.get("body") or "",
        title=payload.get("title"),
        visibility=payload.get("visibility"),
    )
    return jsonify(result), 200


@connections_bp.route("/publish", methods=["POST"])
def publish_route():
    from backend.models import db
    from backend.services.connections import media as media_util, publish_service

    payload = request.get_json(silent=True) or {}
    try:
        result = publish_service.queue_publish(
            connection_ids=payload.get("connection_ids") or [],
            document_ids=payload.get("document_ids") or [],
            body=payload.get("body") or "",
            title=payload.get("title"),
            link_url=payload.get("link_url"),
            tags=payload.get("tags") or [],
            visibility=payload.get("visibility"),
            requested_by=payload.get("requested_by") or "ui",
        )
    except (ValueError, media_util.MediaResolveError) as e:
        return _error(str(e), 400)
    except RuntimeError as e:
        return _error(str(e), 409)
    except SQLAlchemyError as e:
        db.session.rollback()
        return _error("Database error", 500, details=str(e))
    return jsonify(result), 202


@connections_bp.route("/publishes", methods=["GET"])
def list_publishes():
    from backend.models import PublishRecord

    query = PublishRecord.query
    platform = request.args.get("platform")
    status = request.args.get("status")
    document_id = request.args.get("document_id", type=int)
    if platform:
        query = query.filter_by(platform=platform)
    if status:
        query = query.filter_by(status=status)
    if document_id:
        query = query.filter_by(document_id=document_id)

    limit = min(request.args.get("limit", default=50, type=int), 200)
    rows = query.order_by(PublishRecord.created_at.desc()).limit(limit).all()
    return jsonify({"publishes": [r.to_dict() for r in rows]}), 200


@connections_bp.route("/publishes/<int:pid>", methods=["GET"])
def get_publish(pid):
    record, err = _get_record(pid)
    if err:
        return err
    return jsonify(record.to_dict()), 200


@connections_bp.route("/publishes/<int:pid>/approve", methods=["POST"])
def approve_publish(pid):
    from backend.services.connections import publish_service

    record, err = _get_record(pid)
    if err:
        return err
    try:
        result = publish_service.approve(record)
    except ValueError as e:
        return _error(str(e), 400)
    except RuntimeError as e:
        return _error(str(e), 409)
    return jsonify(result), 202


@connections_bp.route("/publishes/<int:pid>/reject", methods=["POST"])
def reject_publish(pid):
    from backend.services.connections import publish_service

    record, err = _get_record(pid)
    if err:
        return err
    try:
        publish_service.reject(record, (request.get_json(silent=True) or {}).get("reason") or "")
    except ValueError as e:
        return _error(str(e), 400)
    return jsonify(record.to_dict()), 200


@connections_bp.route("/publishes/<int:pid>/cancel", methods=["POST"])
def cancel_publish(pid):
    from backend.services.connections import publish_service

    record, err = _get_record(pid)
    if err:
        return err
    try:
        publish_service.cancel(record)
    except ValueError as e:
        return _error(str(e), 400)
    return jsonify(record.to_dict()), 200


# --- settings ----------------------------------------------------------------
@connections_bp.route("/settings", methods=["GET"])
def get_publish_settings():
    from backend.services.connections import gates

    return jsonify(
        {
            "publish_enabled": gates.publish_enabled(),
            "publish_supervised": gates.publish_supervised(),
        }
    ), 200


@connections_bp.route("/settings", methods=["POST"])
def set_publish_settings():
    from backend.services.connections import gates

    payload = request.get_json(silent=True) or {}
    if "publish_enabled" in payload:
        gates.set_publish_enabled(bool(payload["publish_enabled"]))
    if "publish_supervised" in payload:
        gates.set_publish_supervised(bool(payload["publish_supervised"]))
    return jsonify(
        {
            "publish_enabled": gates.publish_enabled(),
            "publish_supervised": gates.publish_supervised(),
        }
    ), 200
