"""Self-code repository endpoints.

These routes expose the configured Guaardvark checkout as a read-first code
source. Edits are proposals by default and are applied only through the guarded
pending-fix path.
"""

import logging

from flask import Blueprint, request

from backend.services.guarded_code_service import (
    GuardedCodeError,
    browse_repo_path,
    default_repo_root,
    read_repo_file,
    stage_pending_fix,
)
from backend.utils.response_utils import error_response, success_response

logger = logging.getLogger(__name__)

self_code_bp = Blueprint("self_code", __name__, url_prefix="/api/self-code")


@self_code_bp.route("/config", methods=["GET"])
def get_config():
    root = default_repo_root()
    return success_response(data={
        "enabled": True,
        "repo_root": str(root),
        "mount_path": "/__repo__",
        "mode": "read_first_review_apply",
    })


@self_code_bp.route("/browse", methods=["GET"])
def browse():
    path = request.args.get("path", "")
    try:
        return success_response(data=browse_repo_path(path))
    except GuardedCodeError as e:
        return error_response(str(e), e.status_code, e.code)


@self_code_bp.route("/file", methods=["GET"])
def file_content():
    path = request.args.get("path", "")
    try:
        return success_response(data=read_repo_file(path))
    except GuardedCodeError as e:
        return error_response(str(e), e.status_code, e.code)


@self_code_bp.route("/propose-edit", methods=["POST"])
def propose_edit():
    data = request.get_json() or {}
    path = data.get("path") or data.get("file_path")
    old_text = data.get("old_text")
    new_text = data.get("new_text")
    description = data.get("description") or "Self-code proposed edit"
    if not path:
        return error_response("path is required", 400)
    if old_text is None or new_text is None:
        return error_response("old_text and new_text are required", 400)
    try:
        pending_id = stage_pending_fix(path, old_text, new_text, description)
        return success_response(
            data={"pending_fix_id": pending_id},
            message="Edit staged for review",
        )
    except GuardedCodeError as e:
        return error_response(str(e), e.status_code, e.code)
    except Exception as e:
        logger.error(f"Failed to stage self-code edit: {e}", exc_info=True)
        return error_response(str(e), 500)


@self_code_bp.route("/review", methods=["POST"])
def review_scope():
    data = request.get_json() or {}
    path = data.get("path") or ""
    prompt = data.get("prompt") or "Review this code scope and propose only surgical fixes."
    try:
        relative_path = read_repo_file(path)["relative_path"]
    except GuardedCodeError:
        # Scope may be a directory; validate through browse instead.
        try:
            listing = browse_repo_path(path)
            relative_path = listing.get("relative_path", path)
        except GuardedCodeError as e:
            return error_response(str(e), e.status_code, e.code)

    description = (
        "Self-code review request. "
        f"Scope: {relative_path or 'repository root'}. "
        f"Instruction: {prompt} "
        "Use read/search tools first. If an edit is warranted, stage it as a PendingFix; do not write directly."
    )
    try:
        from backend.celery_app import celery

        task = celery.send_task("self_improvement.run_directed_async", args=[description])
        return success_response(
            data={"task_id": task.id, "status": "dispatched", "scope": relative_path},
            message="Self-code review dispatched",
        )
    except Exception as e:
        logger.error(f"Failed to dispatch self-code review: {e}", exc_info=True)
        return error_response(str(e), 500)
