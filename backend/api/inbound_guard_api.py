"""Settings → Agents → Inbound guard: the mode, the verdicts, and decisions on held changes.

Every mutating route here is under /api/settings/inbound_guard, which auth_guard
keeps closed to other hosts: switching the guard off or approving a held change
must come from this machine or carry the API key.
"""
import logging

from flask import Blueprint, request

from backend.services import inbound_guard_service as guard
from backend.utils.response_utils import error_response, success_response

logger = logging.getLogger(__name__)

inbound_guard_bp = Blueprint("inbound_guard", __name__, url_prefix="/api/settings/inbound_guard")


@inbound_guard_bp.route("", methods=["GET"])
def get_state():
    from backend.models import InboundScan, db

    open_count = 0
    try:
        open_count = db.session.query(InboundScan.id).filter_by(status="open").count()
    except Exception as exc:
        logger.warning("inbound guard: could not count open verdicts: %s", exc)
    from backend.services import inbound_guard_watch

    return success_response({
        "mode": guard.get_mode(),
        "modes": list(guard.MODES),
        "open": open_count,
        "providers": guard.registered(),
        "git": guard.git_hooks_status(),
        "sweep": inbound_guard_watch.last_summary(),
    })


@inbound_guard_bp.route("", methods=["POST"])
def set_state():
    data = request.get_json(silent=True) or {}
    try:
        mode = guard.set_mode(str(data.get("mode", "")))
    except ValueError as exc:
        return error_response(str(exc), 400)
    return success_response({"mode": mode})


@inbound_guard_bp.route("/scans", methods=["GET"])
def list_scans():
    from backend.models import InboundScan, db

    status = request.args.get("status", "open")
    limit = max(1, min(int(request.args.get("limit", 50)), 200))
    query = db.session.query(InboundScan).order_by(InboundScan.created_at.desc())
    if status != "all":
        query = query.filter_by(status=status)
    rows = [row.to_dict() for row in query.limit(limit).all()]
    git_records = guard.git_ledger(limit) if request.args.get("git", "1") != "0" else []
    approved = {r.get("digest") for r in git_records if r.get("kind") == "approval"}
    git_verdicts = [
        {**r, "approved": r.get("digest") in approved}
        for r in reversed(git_records)
        if r.get("kind") == "verdict" and r.get("verdict") != "allow"
    ]
    return success_response({"scans": rows, "git": git_verdicts})


@inbound_guard_bp.route("/scans/<int:scan_id>", methods=["GET"])
def get_scan(scan_id):
    from backend.models import InboundScan, db

    row = db.session.get(InboundScan, scan_id)
    if row is None:
        return error_response("No such inbound verdict", 404)
    return success_response(row.to_dict(include_payload=True))


@inbound_guard_bp.route("/scans/<int:scan_id>/decide", methods=["POST"])
def decide_scan(scan_id):
    from backend.services.guarded_code_service import GuardedCodeError

    data = request.get_json(silent=True) or {}
    try:
        result = guard.decide(
            scan_id,
            str(data.get("decision", "")),
            by=str(data.get("by") or "operator"),
            note=str(data.get("note") or ""),
            override_block=bool(data.get("override_block")),
        )
    except LookupError as exc:
        return error_response(str(exc), 404)
    except ValueError as exc:
        return error_response(str(exc), 400)
    except GuardedCodeError as exc:
        return error_response(str(exc), exc.status_code, exc.code)
    return success_response(result)


@inbound_guard_bp.route("/git/<digest>/approve", methods=["POST"])
def approve_git(digest):
    data = request.get_json(silent=True) or {}
    if not digest.isalnum() or len(digest) > 40:
        return error_response("Not a verdict digest", 400)
    guard.approve_git(digest, str(data.get("by") or "operator"), str(data.get("note") or ""))
    return success_response({"digest": digest, "approved": True})


@inbound_guard_bp.route("/sweep", methods=["POST"])
def sweep_now():
    """Read every watched file that changed since the last sweep, now."""
    from backend.services import inbound_guard_watch

    if not guard.is_on():
        return error_response("The inbound guard is off; turn it on to sweep.", 409)
    return success_response(inbound_guard_watch.sweep())
