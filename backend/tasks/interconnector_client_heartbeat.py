"""Server-side client heartbeat — keeps a worker node's liveness up to date
WITHOUT depending on a browser tab being open.

This periodic task enrols a client node with its master and heartbeats on a
fixed cadence from the backend, so a node stays "online" as long as its backend
runs. The Settings page runs the same step through /api/interconnector/enrol.

Enrolment (backend/utils/node_auth.py): the node sends the fleet key, its
public key and a signature by that key. Until the operator approves it on the
master the answer is "pending"; then the master returns a certificate, which
is saved with the master's key (pinned for that master URL) and renewed
through the heartbeat. Every heartbeat is signed with it. A refused
heartbeat (expired, revoked, forgotten) sends the node back to enrolment.

Runs on every node; it early-returns cheaply unless this node is a configured,
enabled client with a master URL. Scheduled from celery_app beat_schedule.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time

import requests
from celery import shared_task

log = logging.getLogger(__name__)

# HardwareDetector.detect() probes CPU/GPU/disk (~200ms). Cache it so a 60s
# heartbeat doesn't re-probe every minute.
_profile_cache: dict = {"profile": None, "expires": 0.0}
_PROFILE_TTL_S = 600


def _load_config() -> dict:
    from backend.models import db, Setting
    try:
        setting = db.session.get(Setting, "interconnector_config")
        if setting and setting.value:
            return json.loads(setting.value) if isinstance(setting.value, str) else setting.value
    except Exception as exc:  # never let config read crash the beat task
        log.debug("[SYNC] client-heartbeat: config read failed: %s", exc)
    return {}


def _local_profile() -> dict:
    now = time.time()
    if _profile_cache["profile"] is not None and _profile_cache["expires"] > now:
        return _profile_cache["profile"]
    from backend.services.hardware_detector import HardwareDetector
    profile = HardwareDetector().detect()
    _profile_cache["profile"] = profile
    _profile_cache["expires"] = now + _PROFILE_TTL_S
    return profile


REGISTER_PATH = "/api/interconnector/nodes/register"


def _json_data(resp) -> dict:
    try:
        body = resp.json()
    except ValueError:
        return {}
    if not isinstance(body, dict):
        return {}
    data = body.get("data")
    return data if isinstance(data, dict) else {}


def _error_message(resp) -> str:
    try:
        error = (resp.json() or {}).get("error")
    except ValueError:
        return ""
    if isinstance(error, dict):
        return str(error.get("message") or "")
    return str(error or "")


def _accept_certificate(data: dict, master_url: str, state: dict, public_key: str) -> dict:
    """Check and save a certificate the master sent. Returns the new state,
    or {"error": ...} when it must not be used."""
    from backend.utils import node_auth

    master_key = data.get("master_public_key") or ""
    pinned = state.get("master_public_key") if state.get("master_url") == master_url else None
    if pinned and master_key != pinned:
        log.error("[SYNC] client-heartbeat: the master at %s answered with another key than the "
                  "one pinned at enrolment; refusing its certificate", master_url)
        return {"error": "master_key_changed"}
    try:
        cert = node_auth.read_certificate(data.get("certificate") or "", master_key)
    except node_auth.NodeAuthError as exc:
        return {"error": "bad_certificate", "message": exc.reason}
    if cert.pub != public_key:
        return {"error": "bad_certificate", "message": "The certificate names another key."}
    new_state = {
        "master_url": master_url,
        "master_public_key": master_key,
        "master_node_id": data.get("master_node_id") or cert.iss,
        "node_id": cert.node,
        "certificate": cert.token,
        "certificate_expires": cert.exp,
        "status": "approved",
    }
    node_auth.write_fleet_state(new_state)
    return new_state


def _enrol(master_url: str, config: dict, node_id: str, node_name: str,
           profile: dict, state: dict) -> dict:
    """Ask the master to enrol this machine. {"approved": True, ...} once the
    operator has approved it (the certificate is saved), {"pending": True, ...}
    until then, {"error": ...} when the master refuses."""
    from backend.utils import node_auth

    key = node_auth.load_or_create_private_key()
    public_key = node_auth.public_key_text(key)
    fingerprint = node_auth.fingerprint(public_key)
    # The master keys on the profile's node id; it must be the id the master
    # gave this machine, when that differs from the local one.
    body = {
        "node_id": node_id,
        "node_name": node_name,
        "node_mode": "client",
        "hardware_profile": dict(profile or {}, node_id=node_id),
        "client_port": int(os.environ.get("FLASK_PORT", 5000)),
        "sync_entities": config.get("sync_entities", []),
        "public_key": public_key,
    }
    headers = {"Content-Type": "application/json"}
    if config.get("master_api_key"):
        headers["X-API-Key"] = config["master_api_key"]
    resp = requests.post(
        f"{master_url}{REGISTER_PATH}",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        timeout=10,
        auth=node_auth.NodeSignature(key, node_id),
    )
    data = _json_data(resp)
    kept = {k: state[k] for k in ("master_public_key", "master_node_id") if k in state}
    if resp.status_code == 202 or data.get("status") == "pending":
        assigned = data.get("node_id") or node_id
        node_auth.write_fleet_state({**kept, "master_url": master_url, "node_id": assigned,
                                     "status": "pending"})
        log.info("[SYNC] client-heartbeat: waiting for approval on the master (node %s, key %s)",
                 assigned, fingerprint)
        return {"pending": True, "node_id": assigned, "fingerprint": fingerprint}
    if not resp.ok or data.get("status") != "approved":
        message = _error_message(resp)
        node_auth.write_fleet_state({**kept, "master_url": master_url, "node_id": node_id,
                                     "status": "refused", "message": message})
        log.warning("[SYNC] client-heartbeat: enrolment refused (HTTP %s): %s",
                    resp.status_code, message)
        return {"error": f"enrol_status_{resp.status_code}", "message": message,
                "fingerprint": fingerprint}
    accepted = _accept_certificate(data, master_url, state, public_key)
    if "error" in accepted:
        return {**accepted, "fingerprint": fingerprint}
    log.info("[SYNC] client-heartbeat: enrolled as node %s", accepted["node_id"])
    return {"approved": True, "node_id": accepted["node_id"], "fingerprint": fingerprint}


def _heartbeat(master_url: str, state: dict, payload: dict):
    from backend.utils import node_auth

    signer = node_auth.NodeSignature(
        node_auth.load_or_create_private_key(), state["node_id"], state["certificate"]
    )
    return requests.post(
        f"{master_url}/api/interconnector/nodes/{state['node_id']}/heartbeat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=10,
        auth=signer,
    )


def _do_client_heartbeat() -> dict:
    config = _load_config()
    if not config.get("is_enabled") or config.get("node_mode") != "client":
        return {"skipped": "not_enabled_client"}
    master_url = (config.get("master_url") or "").rstrip("/")
    if not master_url:
        return {"skipped": "no_master_url"}

    from backend.utils import node_auth
    from backend.utils.node_identity import local_node_id

    profile = _local_profile()
    state = node_auth.read_fleet_state()
    if state.get("master_url") != master_url:
        # Another master: its key is pinned, and its certificate issued, anew.
        state = {}
    node_id = state.get("node_id") or local_node_id()
    if not node_id:
        return {"skipped": "no_node_id"}

    node_name = config.get("node_name") or socket.gethostname()
    payload = {"hardware_profile": profile, "sync_entities": config.get("sync_entities", [])}
    try:
        if not node_auth.certificate_usable(state):
            result = _enrol(master_url, config, node_id, node_name, profile, state)
            if not result.get("approved"):
                return result
            state = node_auth.read_fleet_state()
        resp = _heartbeat(master_url, state, payload)
        if resp.status_code in (401, 403, 404):
            # Expired, revoked or forgotten on the master: enrol again.
            log.info("[SYNC] client-heartbeat: master refused the certificate (HTTP %s); "
                     "enrolling again", resp.status_code)
            stale = {k: v for k, v in state.items() if k not in ("certificate", "certificate_expires")}
            node_auth.write_fleet_state(stale)
            result = _enrol(master_url, config, state["node_id"], node_name, profile, stale)
            if not result.get("approved"):
                return result
            state = node_auth.read_fleet_state()
            resp = _heartbeat(master_url, state, payload)
        if resp.ok:
            data = _json_data(resp)
            if data.get("certificate"):
                renewed = _accept_certificate(data, master_url, state, node_auth.local_public_key())
                if "error" in renewed:
                    log.warning("[SYNC] client-heartbeat: renewed certificate refused: %s", renewed)
            log.debug("[SYNC] client-heartbeat: ok (node %s -> %s)", state["node_id"], master_url)
            return {"ok": True, "node_id": state["node_id"]}
        return {"error": f"master_status_{resp.status_code}", "node_id": state["node_id"]}
    except requests.RequestException as exc:
        # Master unreachable is normal/transient — don't spam ERROR.
        log.debug("[SYNC] client-heartbeat: master unreachable: %s", exc)
        return {"error": "master_unreachable"}


@shared_task(name="interconnector.client_heartbeat")
def interconnector_client_heartbeat() -> dict:
    # Reuse the active app context in production; build a minimal one when
    # called directly (tests/scripts). Mirrors cluster_heartbeat_sweeper.
    try:
        from flask import current_app
        current_app._get_current_object()  # raises if no context
        return _do_client_heartbeat()
    except RuntimeError:
        pass
    from backend.celery_app import create_minimal_celery_flask_app
    with create_minimal_celery_flask_app().app_context():
        return _do_client_heartbeat()
