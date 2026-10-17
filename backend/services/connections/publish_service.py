"""Queue publishes as Task-backed jobs.

One PublishRecord and one Task per target: a failed YouTube upload must not
roll back a successful Bluesky post, and per-connection Tasks give each
platform its own progress bar on the Jobs page.

A record moves awaiting_approval -> (approved) -> queued -> processing ->
posted, or waits as ``scheduled`` in between when it has a time (see
scheduling.py). Nothing leaves awaiting_approval without ``approve()`` or, when
approval is switched off, the person's own Publish click in the UI.

While it waits, ``edit()`` may change what the composer sets. Each saved edit
raises ``revision``, and ``approve()`` takes the revision the person was shown,
so an approval never releases text it did not show.
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

# What a person may change on a publish waiting for approval: what the
# composer sets. Media stays as queued.
EDITABLE_FIELDS = (
    "body", "title", "link_url", "tags", "visibility", "post_options", "ai_disclosure", "scheduled_at",
)

# PublishChanged.code values.
EDITED_SINCE = "edited_since"
NOT_WAITING = "not_waiting"

APPROVE_EDITED_MESSAGE = (
    "This publish was edited after it was shown to you. Review the text it has now, "
    "then approve it again."
)
EDIT_CONFLICT_MESSAGE = (
    "This publish was edited elsewhere after you opened it. Open it again to start "
    "from its current text."
)


class PublishChanged(RuntimeError):
    """The publish changed after the caller read it. ``code`` says how:
    ``EDITED_SINCE`` (another edit was saved) or ``NOT_WAITING`` (it was
    approved, rejected or cancelled meanwhile)."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def preflight(
    connection_ids: List[int],
    document_ids: List[int],
    *,
    body: str = "",
    title: Optional[str] = None,
    visibility: Optional[str] = None,
    visibilities: Optional[Dict[str, Any]] = None,
    scheduled_at: Optional[datetime] = None,
    ai_disclosure: Optional[bool] = None,
    requested_by: str = "ui",
    post_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate without queueing, for live feedback in the compose modal.

    Each target's entry carries ``visibility``, the one it would be sent with.
    """
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
        problems = connection_problems(connection)
        entry: Dict[str, Any] = {
            "provider": connection.provider,
            "label": connection.display_name or connection.provider,
        }
        if not problems:
            spec = registry.spec_for(connection.provider)
            caps = spec.capabilities
            target_visibility = visibility_for(caps, chosen_visibility(cid, visibility, visibilities))
            disclosed, required = disclosure_for(caps, items, requested_by, ai_disclosure)
            final_body = compose_body(body, items, connection, caps, disclosed)
            problems = media_util.validate_against(
                caps, items, body=final_body, title=title, visibility=target_visibility
            )
            problems += post_field_problems(spec, options_for(spec, post_options))
            problems += schedule_problems(caps, scheduled_at)
            problems += bot_label_problems(caps, connection)
            # A post that needs no approval and has no time goes out at once,
            # so its account's limit applies now.
            if scheduled_at is None and not gates.requires_approval(requested_by, connection):
                verdict = rate_limits.check(connection.id, caps.rate_limits)
                if not verdict.allowed:
                    problems.append(verdict.reason)
            entry["body"] = final_body
            entry["visibility"] = target_visibility
            entry["ai_disclosure"] = {
                **caps.ai_disclosure.to_dict(),
                "required": required,
                "applied": disclosed,
            }
            if scheduled_at is not None:
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


def chosen_visibility(connection_id, visibility: Optional[str], visibilities: Optional[Dict[str, Any]]):
    """The visibility a person picked for one target: its own entry in
    ``visibilities`` ({connection id: value}), else ``visibility``, which
    applies to every target, else None."""
    if isinstance(visibilities, dict):
        own = visibilities.get(str(connection_id), visibilities.get(connection_id))
        if own is not None and str(own).strip():
            return own
    return visibility


def visibility_for(caps, chosen) -> str:
    """What a target is sent with: the chosen visibility, or the platform's
    declared default when none was chosen. Stored on the record as such, so
    the record shows what went out."""
    text = "" if chosen is None else str(chosen).strip()
    return text or caps.default_visibility


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


def options_for(spec, post_options: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """The composer's per-post values that ``spec`` declares, trimmed; the
    rest belong to other targets of the same post."""
    if not isinstance(post_options, dict):
        return {}
    out: Dict[str, str] = {}
    for post_field in spec.post_fields:
        value = post_options.get(post_field.name)
        if isinstance(value, (str, int, float)) and str(value).strip():
            out[post_field.name] = str(value).strip()
    return out


def post_field_problems(spec, options: Dict[str, str]) -> List[str]:
    problems: List[str] = []
    for post_field in spec.post_fields:
        problems += post_field.problems(options.get(post_field.name, ""))
    return problems


def schedule_problems(caps, scheduled_at: Optional[datetime]) -> List[str]:
    """A time on a post to a platform whose terms rule out scheduled posts."""
    if scheduled_at is not None and caps.schedule_refusal:
        return [caps.schedule_refusal]
    return []


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


def connection_problems(connection) -> List[str]:
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
    visibilities: Optional[Dict[str, Any]] = None,
    requested_by: str = "ui",
    scheduled_at: Optional[datetime] = None,
    ai_disclosure: Optional[bool] = None,
    post_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create one PublishRecord (+ Task) per connection and release or hold them.

    Every target is validated before any record is written, so a refusal for
    one destination does not leave the others half-queued. A target takes its
    entry in ``visibilities`` ({connection id: value}), else ``visibility``,
    else its platform's default (``visibility_for``).

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

        problems = connection_problems(connection)
        spec = registry.spec_for(connection.provider)
        caps = spec.capabilities
        target_visibility = visibility_for(caps, chosen_visibility(cid, visibility, visibilities))
        disclosed, _required = disclosure_for(caps, items, requested_by, ai_disclosure)
        final_body = compose_body(body, items, connection, caps, disclosed)
        problems += media_util.validate_against(
            caps, items, body=final_body, title=title, visibility=target_visibility
        )
        options = options_for(spec, post_options)
        problems += post_field_problems(spec, options)
        problems += schedule_problems(caps, scheduled_at)
        problems += bot_label_problems(caps, connection)
        if problems:
            raise ValueError(f"{spec.label}: {problems[0]}")
        if not needs_approval and scheduled_at is None:
            verdict = rate_limits.check(connection.id, caps.rate_limits)
            if not verdict.allowed:
                raise RuntimeError(f"{spec.label}: {verdict.reason}")
        planned.append(
            (connection, spec, target_visibility, final_body, disclosed, options, needs_approval)
        )

    queued: List[Dict[str, Any]] = []
    for connection, spec, target_visibility, final_body, disclosed, options, needs_approval in planned:
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
            post_options=json.dumps(options) if options else None,
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


def approve(record, revision: Optional[int] = None) -> Dict[str, Any]:
    """Release a publish waiting for approval, or a held one: now, or for its time.

    ``revision`` is the one the person was shown; None means the text as
    queued (revision 0). The approval is stamped by a compare-and-set on
    status and revision, so a save from ``edit()`` landing first refuses it,
    and one landing after finds the record no longer waiting.

    Raises PublishChanged when the record was edited since that revision,
    RuntimeError while public posting is stopped, or when a post that would go
    out now is over its account's rate limit; the record stays put.
    """
    from backend.models import Connection, PublishRecord, db

    if record.status != "awaiting_approval":
        raise ValueError(f"Cannot approve a record that is '{record.status}'.")
    seen = 0 if revision is None else _whole_revision(revision)
    if (record.revision or 0) != seen:
        raise PublishChanged(APPROVE_EDITED_MESSAGE, EDITED_SINCE)
    stopped = gates.posting_stop_reason()
    if stopped:
        raise RuntimeError(stopped)
    connection = Connection.query.get(record.connection_id)
    if connection is None:
        raise ValueError("The target connection no longer exists.")

    now = datetime.now()
    stamped = (
        PublishRecord.query.filter(
            PublishRecord.id == record.id,
            PublishRecord.status == "awaiting_approval",
            PublishRecord.revision == seen,
        ).update({"approved_at": now}, synchronize_session=False)
    )
    if stamped != 1:
        db.session.rollback()
        db.session.refresh(record)
        if record.status != "awaiting_approval":
            raise ValueError(f"Cannot approve a record that is '{record.status}'.")
        raise PublishChanged(APPROVE_EDITED_MESSAGE, EDITED_SINCE)
    record.approved_at = now
    try:
        return _release(record, connection, registry.spec_for(connection.provider))
    except RuntimeError:
        # A refusal leaves the record waiting, without the approval stamp.
        db.session.rollback()
        raise


def _whole_revision(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise ValueError("revision must be a whole number.") from e


def edit_changes(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The editable fields present in a request body, in the types the record keeps.

    A field left out stays as it is. ``scheduled_at`` null clears the time, so
    an approval sends it at once; ``visibility`` null means the platform's
    default. Raises ValueError for a value of the wrong shape or an unreadable time.
    """
    changes: Dict[str, Any] = {}
    if "body" in payload:
        changes["body"] = "" if payload["body"] is None else str(payload["body"])
    for key in ("title", "link_url", "visibility"):
        if key in payload:
            text = "" if payload[key] is None else str(payload[key])
            if key != "title":
                text = text.strip()
            changes[key] = text if text.strip() else None
    if "tags" in payload:
        tags = payload["tags"]
        if isinstance(tags, str):
            tags = tags.split(",")
        if tags is None:
            tags = []
        if not isinstance(tags, list):
            raise ValueError("tags must be a list of words.")
        changes["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    if "post_options" in payload:
        options = payload["post_options"]
        if options is not None and not isinstance(options, dict):
            raise ValueError("post_options must be an object of field names and values.")
        changes["post_options"] = options or {}
    if "ai_disclosure" in payload:
        changes["ai_disclosure"] = bool(payload["ai_disclosure"])
    if "scheduled_at" in payload:
        changes["scheduled_at"] = scheduling.parse_when(payload["scheduled_at"])
    return changes


def _stored_values(record) -> Dict[str, Any]:
    return {
        "body": record.body or "",
        "title": record.title,
        "link_url": record.link_url,
        "tags": _json_list(record.tags),
        "visibility": record.visibility,
        "post_options": _json_dict(record.post_options),
        "ai_disclosure": bool(record.ai_disclosure),
        "scheduled_at": record.scheduled_at,
    }


def _record_media(record):
    """The record's media as queued, with each document's model credit read
    again: the stored refs do not keep it, and a recomposed body must."""
    items = media_util.media_from_refs(record.media_refs)
    for item in items:
        item.attribution = media_util.attribution_of(item.document_id)
    return items


def _check_edit(record, changes: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """Validate the record with ``changes`` applied as ``queue_publish``
    validates one target, without saving.

    Returns (report, values, label): the preflight shape for this one target
    plus ``changed`` and ``revision``, the values to store, and the platform's
    label for messages. The publish switch, the posting stop and rate limits
    are not judged here: an edit sends nothing, and ``approve()`` applies them.
    """
    from backend.models import Connection

    stored = _stored_values(record)
    wanted = {**stored, **changes}
    scheduled_at = wanted["scheduled_at"]
    general: List[str] = []
    # A time already passed may stay; approving then sends it at once.
    if scheduled_at != record.scheduled_at:
        general += scheduling.problems(scheduled_at)

    connection = Connection.query.get(record.connection_id) if record.connection_id else None
    entry: Dict[str, Any] = {
        "provider": record.platform,
        "label": (connection.display_name or connection.provider) if connection else record.platform,
    }
    report = {
        "per_connection": {str(record.connection_id or 0): entry},
        "violations": general,
        "changed": [],
        "revision": record.revision or 0,
    }
    if connection is None:
        entry["violations"] = ["The target connection no longer exists."]
        report["ok"] = False
        return report, {}, record.platform
    problems = connection_problems(connection)
    if problems:
        entry["violations"] = problems
        report["ok"] = False
        try:
            return report, {}, registry.spec_for(connection.provider).label
        except KeyError:
            return report, {}, connection.provider

    spec = registry.spec_for(connection.provider)
    caps = spec.capabilities
    items = _record_media(record)
    disclosed, required = disclosure_for(caps, items, record.requested_by, wanted["ai_disclosure"])
    body = compose_body(wanted["body"], items, connection, caps, disclosed)
    visibility = visibility_for(caps, wanted["visibility"])
    options = options_for(spec, wanted["post_options"])
    problems = media_util.validate_against(
        caps, items, body=body, title=wanted["title"], visibility=visibility
    )
    problems += post_field_problems(spec, options)
    problems += schedule_problems(caps, scheduled_at)
    problems += bot_label_problems(caps, connection)

    entry["body"] = body
    entry["visibility"] = visibility
    entry["ai_disclosure"] = {**caps.ai_disclosure.to_dict(), "required": required, "applied": disclosed}
    if scheduled_at is not None and scheduled_at > datetime.now():
        mode = scheduling.plan(caps, scheduled_at, visibility)
        entry["schedule"] = {"mode": mode, "note": scheduling.describe(caps, spec.label, mode)}
    entry["violations"] = problems

    values = {
        "body": body,
        "title": wanted["title"],
        "link_url": wanted["link_url"],
        "tags": wanted["tags"],
        "visibility": visibility,
        "post_options": options,
        "ai_disclosure": disclosed,
        "scheduled_at": scheduled_at,
    }
    report["changed"] = [name for name in EDITABLE_FIELDS if values[name] != stored[name]]
    report["ok"] = not general and not problems
    return report, values, spec.label


def _not_waiting_message(status: str) -> str:
    return f"This publish is no longer waiting for approval (it is '{status}'), so it cannot be edited."


def edit_preflight(record, changes: Dict[str, Any]) -> Dict[str, Any]:
    """What ``edit()`` would say about ``changes``, for live feedback while editing."""
    if record.status != "awaiting_approval":
        return {
            "ok": False,
            "per_connection": {},
            "violations": [_not_waiting_message(record.status)],
            "changed": [],
            "revision": record.revision or 0,
        }
    return _check_edit(record, changes)[0]


def edit(
    record,
    changes: Dict[str, Any],
    *,
    revision: Optional[int] = None,
    edited_by: str = "ui",
) -> Dict[str, Any]:
    """Change a publish that waits for approval. It keeps waiting; nothing is sent.

    ``changes`` comes from ``edit_changes``; ``revision`` is the one the editor
    started from (None: the current one). The new values pass the same checks
    as queueing, with the same messages, the AI label forced on where the
    platform requires it for this record's source and media. A saved change
    raises ``revision``, so an approval given for the old text is refused.

    Returns {"changed": [field, ...], "publish": record dict}; nothing is
    written when nothing differs.

    Raises:
        ValueError: a value breaks a rule of the target, or the time is too soon.
        PublishChanged: the record left awaiting_approval, or another edit was
            saved since ``revision``.
    """
    from backend.models import PublishRecord, db

    if record.status != "awaiting_approval":
        raise PublishChanged(_not_waiting_message(record.status), NOT_WAITING)
    seen = (record.revision or 0) if revision is None else _whole_revision(revision)
    if (record.revision or 0) != seen:
        raise PublishChanged(EDIT_CONFLICT_MESSAGE, EDITED_SINCE)

    report, values, label = _check_edit(record, changes)
    if report["violations"]:
        raise ValueError(report["violations"][0])
    entry = next(iter(report["per_connection"].values()))
    if entry["violations"]:
        raise ValueError(f"{label}: {entry['violations'][0]}")
    changed = report["changed"]
    if not changed:
        return {"changed": [], "publish": record.to_dict()}

    stored = {name: _column_value(name, values[name]) for name in changed}
    so_far = set(_json_list(record.edited_fields)) | set(changed)
    stored.update({
        "revision": PublishRecord.revision + 1,
        "edited_at": datetime.now(),
        "edited_by": edited_by,
        "edited_fields": json.dumps([name for name in EDITABLE_FIELDS if name in so_far]),
    })
    saved = (
        PublishRecord.query.filter(
            PublishRecord.id == record.id,
            PublishRecord.status == "awaiting_approval",
            PublishRecord.approved_at.is_(None),
            PublishRecord.revision == seen,
        ).update(stored, synchronize_session=False)
    )
    db.session.commit()
    db.session.refresh(record)
    if saved != 1:
        if record.status != "awaiting_approval":
            raise PublishChanged(_not_waiting_message(record.status), NOT_WAITING)
        raise PublishChanged(EDIT_CONFLICT_MESSAGE, EDITED_SINCE)
    return {"changed": changed, "publish": record.to_dict()}


def _column_value(name: str, value):
    """A value from ``_check_edit`` as its PublishRecord column stores it."""
    if name == "tags":
        return json.dumps(value)
    if name == "post_options":
        return json.dumps(value) if value else None
    return value


def _json_list(raw) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except (ValueError, TypeError):
        return []


def _json_dict(raw) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


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
