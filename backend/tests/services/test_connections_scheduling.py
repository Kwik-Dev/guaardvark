"""Scheduled publishing, per-account rate limits, AI disclosure and Delete on platform.

Providers keep their real SPEC; their network functions are replaced, so
nothing here reaches a platform, a broker or Redis.
"""

import json
import sys
import types
from datetime import datetime, timedelta

import pytest

from backend.services.connections import (
    base,
    media as media_util,
    publish_runner,
    publish_service,
    rate_limits,
    registry,
    scheduler,
    scheduling,
    service,
)
from backend.services.social_outreach import kill_switch as ks


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
    """Stubbed provider functions, a recording Celery and an open cadence."""
    calls = []
    cadence = []
    dispatched = []

    executor = types.ModuleType("backend.tasks.unified_task_executor")
    executor.execute_unified_task = types.SimpleNamespace(
        apply_async=lambda args, **kw: dispatched.append(args[0]) or types.SimpleNamespace(id=f"c-{args[0]}")
    )
    monkeypatch.setitem(sys.modules, "backend.tasks.unified_task_executor", executor)
    monkeypatch.setattr(ks, "cadence_allows_post", lambda p: (True, None))
    monkeypatch.setattr(ks, "record_post", lambda p: cadence.append(p))
    monkeypatch.setattr(
        service, "build_ctx",
        lambda c: base.ConnCtx(connection={}, config=json.loads(c.config or "{}"), secrets={}),
    )
    monkeypatch.setattr(service, "redact", lambda text: text)

    for provider in registry.PROVIDER_MODULES:
        real = registry.get_provider(provider)
        module = types.SimpleNamespace(SPEC=real.SPEC, test=real.test)

        def publish(ctx, req, on_progress, provider=provider):
            calls.append(("publish", provider, req))
            return base.PublishResult(ok=True, remote_id=f"{provider}-{req.record_id}", remote_url="https://example.invalid/p")

        module.publish = publish
        if real.SPEC.capabilities.native_scheduling is not None:
            def schedule(ctx, req, on_progress, provider=provider):
                calls.append(("schedule", provider, req))
                return base.PublishResult(ok=True, remote_id=f"held-{req.record_id}")

            def scheduled_state(ctx, remote_id, provider=provider):
                calls.append(("state", provider, remote_id))
                return base.ScheduleCheck(state=base.SCHEDULE_PUBLISHED)

            module.schedule, module.scheduled_state = schedule, scheduled_state
        if real.SPEC.capabilities.deletable:
            def delete(ctx, req, provider=provider):
                calls.append(("delete", provider, req))
                return base.DeleteResult(ok=True, message="deleted")

            module.delete = delete
        monkeypatch.setitem(registry._cache, provider, module)

    return types.SimpleNamespace(calls=calls, cadence=cadence, dispatched=dispatched)


def _connection(provider, config=None):
    from backend.models import Connection, db

    row = Connection(family="social", provider=provider, account_slug="default", status="connected",
                     auth_kind="api_token", config=json.dumps(config or {}))
    db.session.add(row)
    db.session.commit()
    return row


def _record(record_id):
    from backend.models import PublishRecord

    return PublishRecord.query.get(record_id)


def _queue(connection, body="hello", **kwargs):
    result = publish_service.queue_publish(connection_ids=[connection.id], body=body, **kwargs)
    return _record(result["queued"][0]["publish_record_id"])


def _run(task_id):
    from backend.models import Task

    task = Task.query.get(task_id)
    return publish_runner.run({"workflow_config": task.workflow_config}, lambda *a: None)


def _make_due(record, ago=timedelta(minutes=2)):
    """Put a record's time in the past; the runner and ticks read the wall clock."""
    from backend.models import PublishRecord, db

    record.scheduled_at = datetime.now() - ago
    db.session.commit()
    PublishRecord.query.filter_by(id=record.id).update({"updated_at": datetime.now() - timedelta(minutes=10)})
    db.session.commit()


def _document(tmp_path, name, meta):
    from backend.models import Document, db

    path = tmp_path / name
    path.write_bytes(b"\0" * 100)
    doc = Document(filename=name, path=str(path), type=path.suffix, file_metadata=json.dumps(meta) if meta else None)
    db.session.add(doc)
    db.session.commit()
    return doc


LATER = timedelta(hours=2)


# --- scheduling rules ----------------------------------------------------------
def test_times_parse_to_local_and_reject_garbage():
    assert scheduling.parse_when(None) is None
    parsed = scheduling.parse_when("2030-01-01T12:00:00Z")
    assert parsed.tzinfo is None
    assert parsed == datetime.fromisoformat("2030-01-01T12:00:00+00:00").astimezone().replace(tzinfo=None)
    with pytest.raises(ValueError):
        scheduling.parse_when("tomorrow-ish")


def test_a_time_under_a_minute_away_is_refused():
    assert scheduling.problems(datetime.now() + timedelta(seconds=20))
    assert scheduling.problems(datetime.now() + timedelta(minutes=5)) == []


def test_plan_uses_the_platform_where_it_can():
    when = datetime.now() + LATER
    mastodon = registry.spec_for("mastodon").capabilities
    youtube = registry.spec_for("youtube").capabilities
    assert scheduling.plan(mastodon, when, "unlisted") == scheduling.MODE_NATIVE
    assert scheduling.plan(registry.spec_for("bluesky").capabilities, when, "public") == scheduling.MODE_TIMER
    # publishAt only makes a private video public.
    assert scheduling.plan(youtube, when, "public") == scheduling.MODE_NATIVE
    assert scheduling.plan(youtube, when, "private") == scheduling.MODE_TIMER
    # Under the platform's minimum lead plus the queue margin: the timer.
    assert scheduling.plan(mastodon, datetime.now() + timedelta(minutes=6), "unlisted") == scheduling.MODE_TIMER


# --- approval gate -------------------------------------------------------------
def test_scheduled_publish_waits_for_approval_and_the_timer_sends_nothing(db_app, platform):
    record = _queue(_connection("bluesky"), scheduled_at=datetime.now() + LATER)
    assert record.status == "awaiting_approval" and record.approved_at is None

    _make_due(record)
    out = scheduler.tick()

    assert out["released"] is None
    assert record.status == "awaiting_approval"
    assert platform.dispatched == []


def test_timer_publish_goes_out_when_due(db_app, platform):
    record = _queue(_connection("bluesky"), scheduled_at=datetime.now() + LATER)
    publish_service.approve(record)
    assert record.status == "scheduled" and record.approved_at is not None
    assert platform.dispatched == []

    assert scheduler.tick()["released"] is None  # not yet due
    _make_due(record)
    released = scheduler.tick()["released"]
    assert released["publish_record_id"] == record.id

    _run(released["task_id"])
    assert record.status == "posted"
    assert platform.calls[-1][0] == "publish"


def test_native_publish_is_handed_off_then_settled(db_app, platform):
    record = _queue(_connection("mastodon", {"instance_url": "https://m.example"}),
                    scheduled_at=datetime.now() + LATER)
    out = publish_service.approve(record)
    assert record.status == "queued"

    _run(out["task_id"])
    assert record.status == "scheduled" and record.scheduled_natively
    kind, _provider, request = platform.calls[-1]
    assert kind == "schedule" and request.publish_at == record.scheduled_at
    assert platform.cadence == []  # nothing public yet
    with pytest.raises(ValueError, match="Delete on platform"):
        publish_service.cancel(record)

    _make_due(record)
    settled = scheduler.tick()["settled"]
    assert settled == {"publish_record_id": record.id, "state": base.SCHEDULE_PUBLISHED}
    assert record.status == "posted" and record.posted_at == record.scheduled_at
    assert platform.cadence == ["mastodon"]


def test_due_posts_leave_one_at_a_time(db_app, platform):
    first = _queue(_connection("telegram", {"chat_id": "@c"}), scheduled_at=datetime.now() + LATER)
    second = _queue(_connection("discord_webhook"), scheduled_at=datetime.now() + LATER)
    publish_service.approve(first)
    publish_service.approve(second)
    _make_due(first, timedelta(minutes=3))
    _make_due(second, timedelta(minutes=2))

    tick = scheduler.tick()
    assert tick["released"]["publish_record_id"] == first.id and tick["waiting"] == [second.id]
    assert scheduler.tick()["released"] is None  # first still in flight

    _run(tick["released"]["task_id"])
    assert scheduler.tick()["released"]["publish_record_id"] == second.id


def test_due_while_posting_is_stopped_goes_back_to_a_person(db_app, platform):
    record = _queue(_connection("telegram", {"chat_id": "@c"}), scheduled_at=datetime.now() + LATER)
    publish_service.approve(record)
    _make_due(record)
    ks._write_setting(ks.POSTING_STOP_KEY, "true")

    out = scheduler.tick()

    assert record.id in out["held"]
    assert record.status == "awaiting_approval" and record.approved_at is None
    assert platform.dispatched == []


def test_a_post_for_a_since_disabled_account_waits_for_a_person(db_app, platform):
    from backend.models import db

    telegram = _connection("telegram", {"chat_id": "@c"})
    record = _queue(telegram, scheduled_at=datetime.now() + LATER)
    publish_service.approve(record)
    telegram.enabled = False
    db.session.commit()
    _make_due(record)

    out = scheduler.tick()

    assert record.id in out["held"] and record.status == "awaiting_approval"
    assert platform.dispatched == []


def test_a_very_late_post_waits_for_a_person(db_app, platform):
    record = _queue(_connection("telegram", {"chat_id": "@c"}), scheduled_at=datetime.now() + LATER)
    publish_service.approve(record)
    _make_due(record, timedelta(seconds=scheduling.LATE_HOLD_SECONDS + 3600))

    scheduler.tick()

    assert record.status == "awaiting_approval"
    assert "missed its time" in record.error_message


def test_runner_refuses_a_scheduled_record_without_approval(db_app, platform):
    from backend.models import PublishRecord, db

    bluesky = _connection("bluesky")
    row = PublishRecord(connection_id=bluesky.id, platform="bluesky", body="x", status="queued",
                        scheduled_at=datetime.now())
    db.session.add(row)
    db.session.commit()

    out = publish_runner.run({"workflow_config": {"publish_record_id": row.id}}, lambda *a: None)

    assert out["skipped"] is True and out["reason"] == "not approved"
    assert platform.calls == []


def test_an_owned_channel_set_to_skip_approval_schedules_without_a_click(db_app, platform):
    discord = _connection("discord_webhook", {"post_without_approval": True})
    record = _queue(discord, scheduled_at=datetime.now() + LATER)
    assert record.status == "scheduled" and record.approved_at is None

    _make_due(record)
    released = scheduler.tick()["released"]
    assert released["publish_record_id"] == record.id


def test_switching_approval_back_on_holds_a_waiting_post(db_app, platform):
    from backend.models import db

    discord = _connection("discord_webhook", {"post_without_approval": True})
    record = _queue(discord, scheduled_at=datetime.now() + LATER)
    discord.config = json.dumps({"post_without_approval": False})
    db.session.commit()

    _make_due(record)
    out = scheduler.tick()

    assert out["released"] is None and record.id in out["held"]
    assert record.status == "awaiting_approval"
    assert platform.dispatched == []


@pytest.mark.parametrize("source", ["chat", "mcp"])
def test_an_agent_scheduled_post_waits_even_on_a_no_approval_channel(db_app, platform, source):
    discord = _connection("discord_webhook", {"post_without_approval": True})
    record = _queue(discord, scheduled_at=datetime.now() + LATER, requested_by=source)
    assert record.status == "awaiting_approval"


# --- rate limits ---------------------------------------------------------------
def _posted(connection, count, minutes_ago=5):
    from backend.models import PublishRecord, db

    now = datetime.now()
    for i in range(count):
        db.session.add(PublishRecord(connection_id=connection.id, platform=connection.provider, body=str(i),
                                     status="posted", posted_at=now - timedelta(minutes=minutes_ago + i)))
    db.session.commit()


def test_limit_refuses_the_post_over_it_and_says_when(db_app, platform):
    bluesky = _connection("bluesky")
    limits = registry.spec_for("bluesky").capabilities.rate_limits
    _posted(bluesky, 10)

    verdict = rate_limits.check(bluesky.id, limits)
    assert not verdict.allowed and verdict.retry_at is not None
    assert rate_limits.check(bluesky.id, limits, at=datetime.now() + timedelta(hours=2)).allowed
    assert [u["used"] for u in rate_limits.usage(bluesky.id, limits)] == [10, 10]


def test_platform_held_posts_count_at_their_time(db_app, platform):
    from backend.models import PublishRecord, db

    mastodon = _connection("mastodon", {"instance_url": "https://m.example"})
    when = datetime.now() + LATER
    for i in range(4):
        db.session.add(PublishRecord(connection_id=mastodon.id, platform="mastodon", body=str(i), status="scheduled",
                                     scheduled_natively=True, scheduled_at=when + timedelta(minutes=i)))
    db.session.commit()
    limits = registry.spec_for("mastodon").capabilities.rate_limits

    assert rate_limits.check(mastodon.id, limits).allowed  # now is clear
    assert not rate_limits.check(mastodon.id, limits, at=when + timedelta(minutes=10)).allowed


def test_an_uncountable_limit_fails_closed(db_app, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr(rate_limits, "event_times", broken)
    verdict = rate_limits.check(1, registry.spec_for("telegram").capabilities.rate_limits)
    assert verdict == rate_limits.Verdict(allowed=False, reason=rate_limits.UNCHECKABLE_REASON)


def test_approval_over_the_limit_leaves_the_record_waiting(db_app, platform):
    from backend.models import db

    bluesky = _connection("bluesky")
    _posted(bluesky, 10)
    record = _queue(bluesky)

    with pytest.raises(RuntimeError, match="limit"):
        publish_service.approve(record)
    db.session.refresh(record)
    assert record.status == "awaiting_approval" and record.approved_at is None
    assert platform.dispatched == []


def test_a_no_approval_publish_over_the_limit_is_refused_before_any_record(db_app, platform):
    from backend.models import PublishRecord

    discord = _connection("discord_webhook", {"post_without_approval": True})
    _posted(discord, 10)
    before = PublishRecord.query.count()

    with pytest.raises(RuntimeError, match="limit"):
        publish_service.queue_publish(connection_ids=[discord.id], body="one too many")
    assert PublishRecord.query.count() == before


# --- AI disclosure ---------------------------------------------------------------
def test_generated_media_is_recognised(db_app, tmp_path):
    generated = _document(tmp_path, "VideoGen.mp4", {"source": "batch_generation", "batch_id": "b"})
    own = _document(tmp_path, "holiday.mp4", None)
    render = _document(tmp_path, "render.mp4", {"source_document_id": generated.id})

    items = media_util.resolve_media([generated.id, own.id, render.id])

    assert [i.generated for i in items] == [True, False, True]
    assert [i.generated for i in media_util.media_from_refs(media_util.media_refs_json(items))] == [True, False, True]


def test_disclosure_is_forced_where_required_and_a_choice_elsewhere(db_app, platform, tmp_path):
    image = _document(tmp_path, "ImageGen.png", {"source": "batch_generation", "batch_id": "b"})
    mastodon = _connection("mastodon", {"instance_url": "https://m.example"})
    bluesky = _connection("bluesky")

    result = publish_service.preflight([mastodon.id, bluesky.id], [image.id], body="Look")
    forced = result["per_connection"][str(mastodon.id)]
    free = result["per_connection"][str(bluesky.id)]
    assert forced["ai_disclosure"]["required"] and forced["body"].endswith("Made with AI.")
    assert not free["ai_disclosure"]["required"] and not free["ai_disclosure"]["applied"]

    chosen = publish_service.preflight([bluesky.id], [], body="Look", ai_disclosure=True)
    assert chosen["per_connection"][str(bluesky.id)]["body"] == "Look\n\nMade with AI."

    record = _queue(mastodon, body="Look", document_ids=[image.id])
    assert record.ai_disclosure is True and record.body.endswith("Made with AI.")


def test_youtube_sets_its_flag_only_when_disclosing():
    from backend.services.connections.providers import youtube

    item = base.MediaItem(path="/x/clip.mp4", mime="video/mp4", bytes=10)
    on = youtube._video_body(base.PublishRequest(record_id=1, connection_id=1, ai_disclosure=True), item, {})
    off = youtube._video_body(base.PublishRequest(record_id=1, connection_id=1), item, {})
    assert on["status"]["containsSyntheticMedia"] is True
    assert "containsSyntheticMedia" not in off["status"]


# --- Delete on platform ------------------------------------------------------------
def test_delete_removes_a_posted_post(db_app, platform):
    record = _queue(_connection("bluesky"))
    _run(publish_service.approve(record)["task_id"])
    assert record.status == "posted"

    publish_service.delete_remote(record)

    assert record.status == "deleted" and record.deleted_at is not None
    kind, _provider, request = platform.calls[-1]
    assert kind == "delete" and request.remote_id == record.remote_id and not request.pending_schedule


def test_delete_withdraws_a_platform_held_post(db_app, platform):
    record = _queue(_connection("mastodon", {"instance_url": "https://m.example"}),
                    scheduled_at=datetime.now() + LATER)
    _run(publish_service.approve(record)["task_id"])

    publish_service.delete_remote(record)

    assert record.status == "deleted"
    assert platform.calls[-1][2].pending_schedule is True


def test_telegram_delete_window_is_enforced(db_app, platform):
    from backend.models import PublishRecord, db

    telegram = _connection("telegram", {"chat_id": "@c"})
    old = PublishRecord(connection_id=telegram.id, platform="telegram", body="x", status="posted",
                        remote_id="7", posted_at=datetime.now() - timedelta(hours=49))
    db.session.add(old)
    db.session.commit()

    with pytest.raises(ValueError, match="48 hours"):
        publish_service.delete_remote(old)
    assert old.status == "posted"


@pytest.mark.parametrize("status", ["awaiting_approval", "queued", "scheduled", "failed", "deleted"])
def test_only_posted_or_platform_held_posts_can_be_deleted(db_app, platform, status):
    from backend.models import PublishRecord, db

    bluesky = _connection("bluesky")
    row = PublishRecord(connection_id=bluesky.id, platform="bluesky", body="x", status=status, remote_id="r")
    db.session.add(row)
    db.session.commit()

    with pytest.raises(ValueError):
        publish_service.delete_remote(row)


# --- routes -------------------------------------------------------------------------
def test_routes_take_a_time_and_report_usage(db_app, platform):
    from backend.api.connections_api import connections_bp

    db_app.register_blueprint(connections_bp)
    client = db_app.test_client()
    discord = _connection("discord_webhook")

    bad = client.post("/api/connections/publish", json={"connection_ids": [discord.id], "body": "x",
                                                          "scheduled_at": "not a time"})
    assert bad.status_code == 400
    ok = client.post("/api/connections/publish", json={"connection_ids": [discord.id], "body": "x",
                                                         "scheduled_at": "2099-01-01T10:00:00Z"})
    assert ok.status_code == 202

    rows = client.get(f"/api/connections/publishes?connection_id={discord.id}&status=awaiting_approval,scheduled")
    assert [r["scheduled_at"] is not None for r in rows.get_json()["publishes"]] == [True]
    usage = client.get("/api/connections/usage").get_json()["usage"][str(discord.id)]
    assert usage["posts"] == 0 and usage["rate_limits"][0]["posts"] == 10


# --- terms that rule out scheduling, authorization expiry ------------------------
def test_linkedin_refuses_a_scheduled_post(db_app, platform):
    linkedin = _connection("linkedin")
    when = datetime.now() + LATER
    refusal = registry.spec_for("linkedin").capabilities.schedule_refusal

    result = publish_service.preflight([linkedin.id], [], body="x", scheduled_at=when)

    assert result["per_connection"][str(linkedin.id)]["violations"] == [refusal]
    with pytest.raises(ValueError, match="LinkedIn: LinkedIn's API Terms"):
        _queue(linkedin, scheduled_at=when)
    assert _queue(linkedin).status == "awaiting_approval"


def test_test_records_when_authorization_ends_and_when_it_has(db_app, monkeypatch):
    expires = datetime(2030, 1, 1, 12, 0)
    answers = [(True, "ok", {"token_expires_at": expires}), (False, "expired", {"expired": True})]
    module = types.SimpleNamespace(SPEC=registry.spec_for("linkedin"), test=lambda ctx: answers.pop(0))
    monkeypatch.setitem(registry._cache, "linkedin", module)
    monkeypatch.setattr(service, "build_ctx", lambda c: base.ConnCtx(connection={}, config={}, secrets={}))
    monkeypatch.setattr(service, "redact", lambda text: text)
    linkedin = _connection("linkedin")

    assert service.test_connection(linkedin) == (True, "ok")
    assert linkedin.token_expires_at == expires and linkedin.status == "connected"
    assert service.test_connection(linkedin) == (False, "expired")
    assert linkedin.status == "expired"
    assert publish_service.connection_problems(linkedin) == ["expired"]
