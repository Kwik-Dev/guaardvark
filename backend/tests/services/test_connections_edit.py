"""Editing a publish that waits for approval, and approvals tied to what was shown.

Providers keep their real SPEC; their network functions and Celery are
replaced, so nothing here reaches a platform, a broker or Redis.
"""

import dataclasses
import json
import sys
import types
from datetime import datetime, timedelta

import pytest

from backend.services.connections import (
    base,
    gates,
    publish_runner,
    publish_service,
    registry,
    service,
)
from backend.services.social_outreach import kill_switch as ks

LATER = timedelta(days=7)


@pytest.fixture
def db_app(tmp_path, monkeypatch):
    from flask import Flask

    from backend.models import db

    monkeypatch.setenv("GUAARDVARK_CONFIG_DIR", str(tmp_path / "config"))
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def platform(monkeypatch):
    """Stubbed provider functions and a recording Celery."""
    calls = []
    dispatched = []

    executor = types.ModuleType("backend.tasks.unified_task_executor")
    executor.execute_unified_task = types.SimpleNamespace(
        apply_async=lambda args, **kw: dispatched.append(args[0]) or types.SimpleNamespace(id=f"c-{args[0]}")
    )
    monkeypatch.setitem(sys.modules, "backend.tasks.unified_task_executor", executor)
    monkeypatch.setattr(ks, "cadence_allows_post", lambda p: (True, None))
    monkeypatch.setattr(ks, "record_post", lambda p: None)
    monkeypatch.setattr(
        service, "build_ctx",
        lambda c: base.ConnCtx(connection={}, config=json.loads(c.config or "{}"), secrets={}),
    )
    monkeypatch.setattr(service, "redact", lambda text: text)

    for provider in registry.PROVIDER_MODULES:
        real = registry.get_provider(provider)
        module = types.SimpleNamespace(SPEC=real.SPEC, test=real.test)

        def publish(ctx, req, on_progress, provider=provider):
            calls.append((provider, req))
            return base.PublishResult(ok=True, remote_id=f"{provider}-{req.record_id}")

        module.publish = publish
        monkeypatch.setitem(registry._cache, provider, module)

    return types.SimpleNamespace(calls=calls, dispatched=dispatched)


def _connection(provider, config=None):
    from backend.models import Connection, db

    row = Connection(family="social", provider=provider, account_slug="default", status="connected",
                     auth_kind="api_token", config=json.dumps(config or {}))
    db.session.add(row)
    db.session.commit()
    return row


def _queue(connection, body="Drafted by an agent", **kwargs):
    from backend.models import PublishRecord

    result = publish_service.queue_publish(connection_ids=[connection.id], body=body, **kwargs)
    return PublishRecord.query.get(result["queued"][0]["publish_record_id"])


def _edit(record, revision=None, **payload):
    return publish_service.edit(record, publish_service.edit_changes(payload), revision=revision)


def _queue_refusal(connection, **kwargs):
    with pytest.raises((ValueError, RuntimeError)) as refused:
        publish_service.queue_publish(connection_ids=[connection.id], **kwargs)
    return str(refused.value)


def _edit_refusal(record, **payload):
    with pytest.raises(ValueError) as refused:
        _edit(record, **payload)
    return str(refused.value)


# --- what an edit may change, and when ---------------------------------------
def test_an_edit_keeps_the_publish_waiting_and_records_who_and_what(db_app, platform):
    record = _queue(_connection("discord_webhook"), requested_by="chat",
                    scheduled_at=datetime.now() + LATER, link_url="https://example.invalid/a")

    out = publish_service.edit(
        record,
        publish_service.edit_changes({"body": "A person's words", "link_url": ""}),
        revision=0,
        edited_by="ui",
    )

    assert out["changed"] == ["body", "link_url"]
    assert record.status == "awaiting_approval" and record.approved_at is None
    assert record.body == "A person's words" and record.link_url is None
    assert record.revision == 1 and record.edited_by == "ui" and record.edited_at is not None
    assert json.loads(record.edited_fields) == ["body", "link_url"]
    # The source stays: the platform rules for agent-drafted text still apply.
    assert record.requested_by == "chat"
    assert platform.dispatched == [] and platform.calls == []


def test_edited_fields_accumulate_and_an_unchanged_save_writes_nothing(db_app, platform):
    record = _queue(_connection("discord_webhook"), scheduled_at=datetime.now() + LATER)

    assert _edit(record, body="Drafted by an agent")["changed"] == []
    assert record.revision == 0 and record.edited_at is None

    _edit(record, body="New words")
    _edit(record, scheduled_at=None)

    assert record.revision == 2 and record.scheduled_at is None
    assert json.loads(record.edited_fields) == ["body", "scheduled_at"]
    assert record.to_dict()["edited_fields"] == ["body", "scheduled_at"]


@pytest.mark.parametrize(
    "status", ["queued", "scheduled", "processing", "posted", "failed", "rejected", "cancelled", "deleted"]
)
def test_only_a_publish_waiting_for_approval_can_be_edited(db_app, platform, status):
    from backend.models import db

    record = _queue(_connection("discord_webhook"))
    record.status = status
    db.session.commit()

    with pytest.raises(publish_service.PublishChanged) as refused:
        _edit(record, body="Too late")
    assert refused.value.code == publish_service.NOT_WAITING
    assert record.body == "Drafted by an agent" and record.revision == 0
    assert publish_service.edit_preflight(record, {"body": "x"})["ok"] is False


def test_an_approved_post_cannot_be_edited(db_app, platform):
    record = _queue(_connection("discord_webhook"), scheduled_at=datetime.now() + LATER)
    publish_service.approve(record)
    assert record.status == "scheduled"

    with pytest.raises(publish_service.PublishChanged, match="no longer waiting"):
        _edit(record, body="Changed after approval")
    assert record.body == "Drafted by an agent"


def test_an_edit_from_an_old_revision_is_refused(db_app, platform):
    record = _queue(_connection("discord_webhook"))
    _edit(record, revision=0, body="First edit")

    with pytest.raises(publish_service.PublishChanged) as refused:
        _edit(record, revision=0, body="Second tab")
    assert refused.value.code == publish_service.EDITED_SINCE
    assert record.body == "First edit" and record.revision == 1


def test_a_text_edit_to_a_post_whose_time_has_passed_is_allowed(db_app, platform):
    from backend.models import db

    record = _queue(_connection("discord_webhook"), scheduled_at=datetime.now() + LATER)
    record.scheduled_at = datetime.now() - timedelta(hours=8)
    db.session.commit()

    _edit(record, body="Still worth sending")
    assert record.body == "Still worth sending"
    with pytest.raises(ValueError, match="at least a minute"):
        _edit(record, scheduled_at=(datetime.now() - timedelta(hours=1)).isoformat())


# --- the same rules and messages as queueing ---------------------------------
def _titled_platform(monkeypatch):
    """discord_webhook declaring a required, capped title and a per-post field."""
    real = registry.get_provider("discord_webhook")
    spec = dataclasses.replace(
        real.SPEC,
        capabilities=dataclasses.replace(
            real.SPEC.capabilities, supports_title=True, requires_title=True, max_title_chars=10,
        ),
        post_fields=(
            base.PostField(name="topic", label="Topic", required=True, pattern=r"[a-z]+",
                           pattern_message="Topic takes lower-case letters only."),
        ),
    )
    monkeypatch.setitem(registry._cache, "discord_webhook", types.SimpleNamespace(
        SPEC=spec, test=real.test, publish=real.publish,
    ))


@pytest.mark.parametrize(
    "fields",
    [
        {"body": "x" * 2001},
        {"visibility": "private"},
        {"body": ""},
        {"scheduled_at": (datetime.now() + timedelta(seconds=10)).isoformat()},
    ],
    ids=["text-limit", "visibility", "nothing-to-post", "time-too-soon"],
)
def test_an_edit_is_refused_with_queueings_message(db_app, platform, fields):
    discord = _connection("discord_webhook")
    record = _queue(discord, body="fine")
    queue_fields = {"body": "fine", **fields}
    if "scheduled_at" in queue_fields:
        queue_fields["scheduled_at"] = publish_service.scheduling.parse_when(queue_fields["scheduled_at"])

    assert _edit_refusal(record, **fields) == _queue_refusal(discord, **queue_fields)
    assert record.body == "fine" and record.revision == 0


def test_title_rules_and_post_fields_match_queueing(db_app, platform, monkeypatch):
    _titled_platform(monkeypatch)
    discord = _connection("discord_webhook")
    record = _queue(discord, title="Short", post_options={"topic": "news"})

    for fields in ({"title": ""}, {"title": "Far too long a title"}, {"post_options": {"topic": "News!"}},
                   {"post_options": {}}):
        queued = {"title": "Short", "post_options": {"topic": "news"}, **fields}
        assert _edit_refusal(record, **fields) == _queue_refusal(discord, body="Drafted by an agent", **queued)

    _edit(record, title="Better", post_options={"topic": "update", "undeclared": "dropped"})
    assert record.title == "Better" and json.loads(record.post_options) == {"topic": "update"}


def test_a_platform_that_refuses_a_time_refuses_one_on_edit(db_app, platform):
    linkedin = _connection("linkedin")
    record = _queue(linkedin)
    when = (datetime.now() + LATER).isoformat()

    message = _edit_refusal(record, scheduled_at=when)
    assert message.startswith("LinkedIn: ") and record.scheduled_at is None
    assert message == _queue_refusal(linkedin, body="x", scheduled_at=datetime.fromisoformat(when))


def test_a_required_ai_label_stays_on_after_an_edit(db_app, platform):
    mastodon = _connection("mastodon", {"instance_url": "https://m.example"})
    record = _queue(mastodon, body="Agent text", requested_by="chat")
    assert record.ai_disclosure is True and record.body.endswith("Made with AI.")

    report = publish_service.edit_preflight(record, {"ai_disclosure": False, "body": "Rewritten"})
    entry = report["per_connection"][str(mastodon.id)]
    assert entry["ai_disclosure"]["required"] and entry["body"] == "Rewritten\n\nMade with AI."

    _edit(record, body="Rewritten", ai_disclosure=False)
    assert record.ai_disclosure is True and record.body == "Rewritten\n\nMade with AI."


def test_a_chosen_ai_label_can_be_switched_off_where_no_rule_applies(db_app, platform):
    record = _queue(_connection("discord_webhook"), body="Mine", ai_disclosure=True)
    assert record.body == "Mine\n\nMade with AI."

    _edit(record, body="Mine", ai_disclosure=False)
    assert record.ai_disclosure is False and record.body == "Mine"


def test_rate_limits_are_left_to_the_approval(db_app, platform):
    from backend.models import PublishRecord, db

    bluesky = _connection("bluesky")
    now = datetime.now()
    for i in range(10):
        db.session.add(PublishRecord(connection_id=bluesky.id, platform="bluesky", body=str(i),
                                     status="posted", posted_at=now - timedelta(minutes=5 + i)))
    db.session.commit()
    record = _queue(bluesky)

    _edit(record, body="Edited while the account is at its limit")
    with pytest.raises(RuntimeError, match="limit"):
        publish_service.approve(record, revision=1)
    db.session.refresh(record)
    assert record.status == "awaiting_approval" and record.approved_at is None


# --- an approval is tied to the revision it was shown -----------------------
def test_an_approval_names_the_revision_it_was_shown(db_app, platform):
    record = _queue(_connection("discord_webhook"), requested_by="mcp")
    _edit(record, body="What the person wrote")

    with pytest.raises(publish_service.PublishChanged):
        publish_service.approve(record, revision=0)
    with pytest.raises(publish_service.PublishChanged):
        publish_service.approve(record)  # no revision: the text as queued
    assert record.status == "awaiting_approval" and platform.dispatched == []

    out = publish_service.approve(record, revision=1)
    publish_runner.run({"workflow_config": {"publish_record_id": out["publish_record_id"]}}, lambda *a: None)
    assert record.status == "posted"
    assert platform.calls[-1][1].body == "What the person wrote"


def test_an_unedited_post_approves_without_a_revision(db_app, platform):
    record = _queue(_connection("discord_webhook"))
    assert publish_service.approve(record)["status"] == "queued"


def test_an_edit_saved_after_the_approvers_check_refuses_the_approval(db_app, platform, monkeypatch):
    from backend.models import PublishRecord, db

    record = _queue(_connection("discord_webhook"), requested_by="chat")
    real_stop = gates.posting_stop_reason

    def edit_meanwhile():
        monkeypatch.setattr(gates, "posting_stop_reason", real_stop)
        _edit(db.session.get(PublishRecord, record.id), revision=0, body="Saved in another tab")
        return real_stop()

    monkeypatch.setattr(gates, "posting_stop_reason", edit_meanwhile)
    with pytest.raises(publish_service.PublishChanged) as refused:
        publish_service.approve(record, revision=0)

    assert refused.value.code == publish_service.EDITED_SINCE
    row = db.session.get(PublishRecord, record.id)
    assert row.status == "awaiting_approval" and row.approved_at is None
    assert row.body == "Saved in another tab" and platform.dispatched == []


# --- routes ------------------------------------------------------------------
def test_routes_edit_preflight_and_approve(db_app, platform):
    from backend.api.connections_api import connections_bp

    db_app.register_blueprint(connections_bp)
    client = db_app.test_client()
    discord = _connection("discord_webhook")
    queued = client.post("/api/connections/publish", json={
        "connection_ids": [discord.id], "body": "From chat", "requested_by": "chat",
        "scheduled_at": (datetime.now() + LATER).isoformat(),
    })
    pid = queued.get_json()["queued"][0]["publish_record_id"]
    url = f"/api/connections/publishes/{pid}"

    preflight = client.post(f"{url}/preflight", json={"body": "x" * 2001}).get_json()
    assert preflight["ok"] is False and preflight["changed"] == ["body"]
    assert preflight["per_connection"][str(discord.id)]["violations"] == ["Text is 2001 characters; the limit is 2000."]

    refused = client.patch(url, json={"body": "x" * 2001, "revision": 0})
    assert refused.status_code == 400 and refused.get_json()["error"].endswith("the limit is 2000.")
    bad_time = client.patch(url, json={"scheduled_at": "soon-ish", "revision": 0})
    assert bad_time.status_code == 400

    saved = client.patch(url, json={"body": "Reviewed", "revision": 0, "edited_by": "ui"})
    assert saved.status_code == 200
    assert saved.get_json()["publish"]["revision"] == 1 and saved.get_json()["publish"]["edited_by"] == "ui"
    assert saved.get_json()["publish"]["status"] == "awaiting_approval"

    stale = client.post(f"{url}/approve", json={"revision": 0})
    assert stale.status_code == 409 and stale.get_json()["code"] == "edited_since"
    assert stale.get_json()["publish"]["body"] == "Reviewed"

    approved = client.post(f"{url}/approve", json={"revision": 1})
    assert approved.status_code == 202
    late = client.patch(url, json={"body": "After", "revision": 1})
    assert late.status_code == 409 and late.get_json()["code"] == "not_waiting"
    assert client.get(url).get_json()["body"] == "Reviewed"


def test_an_unlabelled_edit_is_recorded_as_unknown(db_app, platform):
    from backend.api.connections_api import connections_bp

    db_app.register_blueprint(connections_bp)
    record = _queue(_connection("discord_webhook"))

    out = db_app.test_client().patch(f"/api/connections/publishes/{record.id}", json={"body": "No source"})
    assert out.status_code == 200 and out.get_json()["publish"]["edited_by"] == "unknown"
