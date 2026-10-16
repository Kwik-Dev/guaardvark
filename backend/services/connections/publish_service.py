"""Queue publishes as Task-backed jobs.

One PublishRecord and one Task per target: a failed YouTube upload must not
roll back a successful Bluesky post, and per-connection Tasks give each
platform its own progress bar on the Jobs page.

A record moves awaiting_approval -> (approved) -> queued -> processing ->
posted, or waits as ``scheduled`` in between when it has a time (see
scheduling.py). Nothing leaves awaiting_approval without ``approve()`` or, when
approval is switched off, the person's own Publish click in the UI.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend.services.connections import (
    gates,
    media as media_util,
    rate_limits,
    registry,
    scheduling,
)
from backend.services.connections.base import DISCLOSURE_TEXT, DeleteRequest

logger = logging.getLogger(__name__)

TASK_TYPE = "connection_publish"
# Video uploads routinely outlast the executor's default soft limit.
PUBLISH_SOFT_TIME_LIMIT = 7200
PUBLISH_TIME_LIMIT = 7500

HELD_MESSAGE = (
    "Held when public posting was stopped. Approve it to send it once posting resumes."
)


def preflight(
    connection_ids: List[int],
    document_ids: List[int],
    *,
    body: str = "",
    title: Optional[str] = None,
    visibility: Optional[str] = None,
    scheduled_at: Optional[datetime] = None,
    ai_disclosure: Optional[bool] = None,
    requested_by: str = "ui",
) -> Dict[str, Any]:
    """Validate without queueing, for live feedback in the compose modal."""
    from backend.models import Connection

    stopped = gates.posting_stop_reason()
    try:
        items = media_util.resolve_media(document_ids)
    except media_util.MediaResolveError as e:
        return {"ok": False, "per_connection": {}, "violations": [v for v in (stopped, str(e)) if v]}

    general = [stopped] if stopped else []
    general += scheduling.problems(scheduled_at)

    per_connection: Dict[str, Any] = {}
    for cid in connection_ids or []:
        connection = Connection.query.get(cid)
        if connection is None:
            per_connection[str(cid)] = {"violations": ["Connection not found."]}
            continue
        problems = _connection_problems(connection)
        entry: Dict[str, Any] = {
            "provider": connection.provider,
            "label": connection.display_name or connection.provider,
        }
        if not problems:
            spec = registry.spec_for(connection.provider)
            caps = spec.capabilities
            disclosed, required = disclosure_for(caps, items, requested_by, ai_disclosure)
            final_body = compose_body(body, items, connection, caps, disclosed)
            problems = media_util.validate_against(
                caps, items, body=final_body, title=title, visibility=visibility
            )
            problems += bot_label_problems(caps, connection)
            # A post that needs no approval and has no time goes out at once,
            # so its account's limit applies now.
            if scheduled_at is None and not gates.requires_approval(requested_by, connection):
                verdict = rate_limits.check(connection.id, caps.rate_limits)
                if not verdict.allowed:
                    problems.append(verdict.reason)
            entry["body"] = final_body
            entry["ai_disclosure"] = {
                **caps.ai_disclosure.to_dict(),
                "required": required,
                "applied": disclosed,
            }
            if scheduled_at is not None:
                target_visibility = visibility or caps.default_visibility
                mode = scheduling.plan(caps, scheduled_at, target_visibility)
                entry["schedule"] = {
                    "mode": mode,
                    "note": scheduling.describe(caps, spec.label, mode),
                }
        entry["violations"] = problems
        per_connection[str(cid)] = entry

    return {
        "ok": (
            not general
            and all(not v["violations"] for v in per_connection.values())
            and bool(per_connection)
        ),
        "per_connection": per_connection,
        "violations": general,
    }


def disclosure_for(caps, items, requested_by: str, choice: Optional[bool]) -> Tuple[bool, bool]:
    """(disclosed, required) for one destination.

    The platform's rule wins where it applies; elsewhere the person's switch
    decides, and it defaults off.
    """
    generated = any(getattr(item, "generated", False) for item in items or [])
    required = caps.ai_disclosure.required_for(generated, requested_by)
    return required or bool(choice), required


def compose_body(body: str, items, connection, caps, disclosed: bool) -> str:
    """The text that goes out: the body, the model credit, and the AI line."""
    text = _body_with_disclosure(body, items, connection)
    rule = caps.ai_disclosure
    if disclosed and rule.mechanism == DISCLOSURE_TEXT and rule.text:
        stripped = (text or "").rstrip()
        if rule.text not in stripped:
            text = f"{stripped}\n\n{rule.text}" if stripped else rule.text
    return text


def approved(record, connection) -> bool:
    """Whether a scheduled record may go out: a person approved it on the
    Approvals page, or it needs no approval (``gates.requires_approval``, asked
    again now, so switching a connection back to approval holds what waits).

    Records without a time are not judged here: they reach the queue only
    through ``approve()`` or the no-approval path, before this column existed
    as well as after.
    """
    if record.scheduled_at is None or record.approved_at is not None:
        return True
    return not gates.requires_approval(record.requested_by, connection)


def bot_label_problems(caps, connection) -> List[str]:
    """Refuse an account whose bot label must be set and is not known to be."""
    label = caps.bot_label
    if not (label.block_when_unset and label.readable):
        return []
    try:
        config = json.loads(connection.config) if isinstance(connection.config, str) else (connection.config or {})
    except (ValueError, TypeError):
        config = {}
    if config.get("bot_label_set") is True:
        return []
    return [
        "This platform asks automated accounts to carry its bot label, and this "
        f"account's label is not set (or not read yet: run Test). {label.how}"
    ]


def _discloses_ai_media(connection) -> bool:
    """Per-connection opt-out for the disclosure line (config.disclose_ai_media,
    default on). Config is the connection's non-secret JSON options."""
    try:
        cfg = json.loads(connection.config) if isinstance(connection.config, str) else (connection.config or {})
    except (ValueError, TypeError):
        cfg = {}
    return bool(cfg.get("disclose_ai_media", True))


def _body_with_disclosure(body: str, items, connection) -> str:
    line = media_util.disclosure_line(items)
    if not line or not _discloses_ai_media(connection):
        return body
    text = (body or "").rstrip()
    if line in text:
        return body
    return f"{text}\n\n{line}" if text else line


def _connection_problems(connection) -> List[str]:
    if not connection.enabled:
        return ["This connection is disabled."]
    if connection.status not in ("connected", "unconfigured"):
        return [connection.error_message or f"Connection is {connection.status}."]
    try:
        registry.spec_for(connection.provider)
    except KeyError:
        return [f"Provider '{connection.provider}' is not available."]
    return []


def queue_publish(
    *,
    connection_ids: List[int],
    document_ids: Optional[List[int]] = None,
    body: str = "",
    title: Optional[str] = None,
    link_url: Optional[str] = None,
    tags: Optional[List[str]] = None,
    visibility: Optional[str] = None,
    requested_by: str = "ui",
    scheduled_at: Optional[datetime] = None,
    ai_disclosure: Optional[bool] = None,
) -> Dict[str, Any]:
    """Create one PublishRecord (+ Task) per connection and release or hold them.

    Every target is validated before any record is written, so a refusal for
    one destination does not leave the others half-queued.

    Raises:
        ValueError: nothing selected, a bad time, or the post violates a target's limits.
        RuntimeError: publishing is gated off, public posting is stopped, or a
            post that would go out now is over its account's rate limit.
    """
    from backend.models import Connection, PublishRecord, db

    if not connection_ids:
        raise ValueError("Select at least one connection to publish to.")

    stopped = gates.posting_stop_reason()
    if stopped:
        raise RuntimeError(stopped)

    if not gates.publish_enabled():
        raise RuntimeError("Publishing is disabled.")

    time_problems = scheduling.problems(scheduled_at)
    if time_problems:
        raise ValueError(time_problems[0])

    items = media_util.resolve_media(document_ids or [])

    planned = []
    for cid in connection_ids:
        connection = Connection.query.get(cid)
        if connection is None:
            raise ValueError(f"Connection {cid} not found.")
        needs_approval = gates.requires_approval(requested_by, connection)

        problems = _connection_problems(connection)
        spec = registry.spec_for(connection.provider)
        caps = spec.capabilities
        target_visibility = visibility or caps.default_visibility
        disclosed, _required = disclosure_for(caps, items, requested_by, ai_disclosure)
        final_body = compose_body(body, items, connection, caps, disclosed)
        problems += media_util.validate_against(
            caps, items, body=final_body, title=title, visibility=target_visibility
        )
        problems += bot_label_problems(caps, connection)
        if problems:
            raise ValueError(f"{spec.label}: {problems[0]}")
        if not needs_approval and scheduled_at is None:
            verdict = rate_limits.check(connection.id, caps.rate_limits)
            if not verdict.allowed:
                raise RuntimeError(f"{spec.label}: {verdict.reason}")
        planned.append((connection, spec, target_visibility, final_body, disclosed, needs_approval))

    queued: List[Dict[str, Any]] = []
    for connection, spec, target_visibility, final_body, disclosed, needs_approval in planned:
        record = PublishRecord(
            connection_id=connection.id,
            platform=connection.provider,
            document_id=items[0].document_id if items else None,
            media_refs=media_util.media_refs_json(items),
            title=title,
            body=final_body,
            link_url=link_url,
            tags=json.dumps(tags or []),
            visibility=target_visibility,
            status="awaiting_approval",
            requested_by=requested_by,
            scheduled_at=scheduled_at,
            ai_disclosure=disclosed,
        )
        db.session.add(record)
        db.session.commit()

        if needs_approval:
            queued.append({
                "publish_record_id": record.id,
                "status": record.status,
                "scheduled_at": scheduling.to_utc_iso(record.scheduled_at),
            })
            continue

        # A person started it from the app, to a connection set to post
        # without approval (gates.posts_without_approval). approved_at stays
        # empty: it records an Approvals click, and the scheduler re-asks
        # gates.requires_approval when a timer post comes due.
        queued.append(_release(record, connection, spec))

    # True when any target waits on the Approvals page; each entry's status
    # says which.
    return {
        "queued": queued,
        "requires_approval": any(entry[-1] for entry in planned),
        "count": len(queued),
    }


def _release(record, connection, spec, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Send an approved record on: now, to the platform's own schedule, or to the timer.

    Raises RuntimeError before committing anything when the post, now or at
    the time the platform would publish it, is over its account's rate limit.
    A timer post is checked when it comes due instead.
    """
    from backend.models import db

    now = now or datetime.now()
    caps = spec.capabilities
    when = record.scheduled_at if record.scheduled_at and record.scheduled_at > now else None
    mode = scheduling.plan(caps, when, record.visibility, now) if when else None

    if mode != scheduling.MODE_TIMER:
        verdict = rate_limits.check(
            connection.id, caps.rate_limits, at=when or now, now=now, exclude_record_id=record.id
        )
        if not verdict.allowed:
            raise RuntimeError(verdict.reason)

    record.error_message = None
    if mode == scheduling.MODE_TIMER:
        record.status = "scheduled"
        db.session.commit()
        return {
            "publish_record_id": record.id,
            "status": record.status,
            "scheduled_at": scheduling.to_utc_iso(record.scheduled_at),
            "platform": connection.provider,
        }
    record.status = "queued"
    db.session.commit()
    return _dispatch(record, connection, spec)


def release_due(record, connection, spec, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Send an approved timer post whose time has come (``scheduler.release_due``)."""
    return _release(record, connection, spec, now=now)


def _dispatch(record, connection, spec) -> Dict[str, Any]:
    """Create the Task row and hand it to Celery."""
    from backend.models import Task, db

    task = Task(
        name=f"Publish to {spec.label}",
        description=f"Publish record {record.id} to {spec.label}",
        status="queued",
        priority=2,
        type=TASK_TYPE,
        workflow_config=json.dumps(
            {"publish_record_id": record.id, "platform": connection.provider}
        ),
        schedule_type="immediate",
        task_handler="connections",
        handler_config={"platform": connection.provider, "source": record.requested_by},
        progress=0,
    )
    db.session.add(task)
    db.session.commit()

    task.job_id = f"task_{task.id}"
    record.task_id = task.id
    db.session.commit()

    try:
        from backend.tasks.unified_task_executor import execute_unified_task

        celery_result = execute_unified_task.apply_async(
            args=[task.id],
            queue="default",
            soft_time_limit=PUBLISH_SOFT_TIME_LIMIT,
            time_limit=PUBLISH_TIME_LIMIT,
        )
    except Exception as exc:  # noqa: BLE001
        task.status = "failed"
        task.error_message = f"Failed to queue publish: {exc}"
        record.status = "failed"
        record.error_message = str(exc)
        db.session.commit()
        logger.exception("Failed to enqueue publish task for record %s", record.id)
        raise RuntimeError(str(exc)) from exc

    # Kept so the stop on public posting can revoke the job by id
    # (kill_switch.hold_connection_publishes).
    task.handler_config = {**(task.handler_config or {}), "celery_task_id": celery_result.id}
    db.session.commit()

    return {
        "publish_record_id": record.id,
        "task_id": task.id,
        "job_id": task.job_id,
        "celery_task_id": celery_result.id,
        "status": record.status,
        "platform": connection.provider,
    }


def approve(record) -> Dict[str, Any]:
    """Release a publish waiting for approval, or a held one: now, or for its time.

    Raises RuntimeError while public posting is stopped, or when a post that
    would go out now is over its account's rate limit; the record stays put.
    """
    from backend.models import Connection, db

    if record.status != "awaiting_approval":
        raise ValueError(f"Cannot approve a record that is '{record.status}'.")
    stopped = gates.posting_stop_reason()
    if stopped:
        raise RuntimeError(stopped)
    connection = Connection.query.get(record.connection_id)
    if connection is None:
        raise ValueError("The target connection no longer exists.")

    record.approved_at = datetime.now()
    try:
        return _release(record, connection, registry.spec_for(connection.provider))
    except RuntimeError:
        # A refusal leaves the record waiting, without the approval stamp.
        db.session.rollback()
        raise


def hold(record, message: str = HELD_MESSAGE) -> None:
    """Send a publish back to awaiting_approval, by default because public posting stopped.

    A held publish needs a person's click to go out, so lifting the stop never
    sends a backlog by itself.
    """
    from backend.models import db

    record.status = "awaiting_approval"
    record.approved_at = None
    record.error_message = message
    db.session.commit()


def hold_queued() -> tuple[int, int]:
    """Hold every publish that is queued and not yet being sent.

    Returns (held, in_flight): in_flight counts publishes already being sent,
    which a hold cannot recall.
    """
    from backend.models import PublishRecord, db

    rows = PublishRecord.query.filter(PublishRecord.status == "queued").all()
    for row in rows:
        row.status = "awaiting_approval"
        row.approved_at = None
        row.error_message = HELD_MESSAGE
    db.session.commit()
    in_flight = PublishRecord.query.filter(PublishRecord.status == "processing").count()
    return len(rows), in_flight


def reject(record, reason: str = "") -> None:
    from backend.models import db

    if record.status not in ("awaiting_approval", "queued"):
        raise ValueError(f"Cannot reject a record that is '{record.status}'.")
    record.status = "rejected"
    record.error_message = reason or "Rejected by the operator."
    db.session.commit()


def cancel(record) -> None:
    from backend.models import db

    if record.status in ("posted", "failed", "cancelled", "rejected"):
        raise ValueError(f"Cannot cancel a record that is '{record.status}'.")
    # The runner checks status once, on entry. Flipping a record that is
    # already mid-flight would report a cancellation that never happened — the
    # publish can still go out and then overwrite itself to 'posted'. Cancel
    # the underlying job instead, which can actually revoke the work.
    if record.status == "processing":
        raise ValueError(
            "This publish is already being sent. Cancel its job from Activity "
            "to stop it."
        )
    if record.status == "scheduled" and record.scheduled_natively:
        raise ValueError(
            "The platform already holds this post for its time. Use Delete on "
            "platform to withdraw it."
        )
    record.status = "cancelled"
    db.session.commit()


def delete_remote(record) -> str:
    """Delete a posted (or natively scheduled) post from its platform.

    Allowed while public posting is stopped: a takedown is not a post. Returns
    the provider's message. Raises ValueError when the record or platform
    cannot be deleted from here, RuntimeError when the platform refused.
    """
    from backend.models import Connection, db

    pending = record.status == "scheduled" and bool(record.scheduled_natively)
    if record.status != "posted" and not pending:
        raise ValueError(f"Only a posted or platform-scheduled publish can be deleted, not '{record.status}'.")
    if not record.remote_id:
        raise ValueError("The platform gave no id for this post, so it cannot be deleted from here.")
    connection = Connection.query.get(record.connection_id) if record.connection_id else None
    if connection is None:
        raise ValueError("The connection that posted this no longer exists.")

    spec = registry.spec_for(connection.provider)
    caps = spec.capabilities
    if not caps.deletable or not registry.supports(connection.provider, "delete"):
        raise ValueError(f"{spec.label} posts cannot be deleted from here.")
    if caps.delete_window_seconds and record.posted_at and not pending:
        age = (datetime.now() - record.posted_at).total_seconds()
        if age > caps.delete_window_seconds:
            hours = caps.delete_window_seconds // 3600
            raise ValueError(f"{spec.label} allows deleting only within {hours} hours of posting.")

    from backend.services.connections import service

    try:
        result = registry.get_provider(connection.provider).delete(
            service.build_ctx(connection),
            DeleteRequest(
                record_id=record.id,
                remote_id=record.remote_id,
                remote_url=record.remote_url,
                pending_schedule=pending,
            ),
        )
    except Exception as e:  # noqa: BLE001 - any provider failure is shown, never a 500
        logger.warning("Delete of publish %s raised: %s", record.id, e, exc_info=True)
        raise RuntimeError(service.redact(str(e))) from e
    if not result.ok:
        raise RuntimeError(service.redact(result.message) or "The platform refused the delete.")

    record.status = "deleted"
    record.deleted_at = datetime.now()
    record.error_message = None
    db.session.commit()
    return result.message
