"""Which visibility each target of a publish is sent with, and that the record says so.

Providers keep their real SPEC; their network functions and Celery are
replaced, so nothing here reaches a platform, a broker or Redis.
"""

import json
import sys
import types
from datetime import datetime, timedelta

import pytest

from backend.services.connections import (
    base,
    publish_runner,
    publish_service,
    registry,
    service,
)
from backend.services.social_outreach import kill_switch as ks

LATER = timedelta(hours=2)


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
    """Stubbed provider functions and a recording Celery; each call keeps its request."""
    calls = []

    executor = types.ModuleType("backend.tasks.unified_task_executor")
    executor.execute_unified_task = types.SimpleNamespace(
        apply_async=lambda args, **kw: types.SimpleNamespace(id=f"c-{args[0]}")
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
            calls.append(("publish", provider, req))
            return base.PublishResult(ok=True, remote_id=f"{provider}-{req.record_id}")

        module.publish = publish
        if real.SPEC.capabilities.native_scheduling is not None:
            def schedule(ctx, req, on_progress, provider=provider):
                calls.append(("schedule", provider, req))
                return base.PublishResult(ok=True, remote_id=f"held-{req.record_id}")

            module.schedule = schedule
            module.scheduled_state = lambda ctx, remote_id: base.ScheduleCheck(state=base.SCHEDULE_PENDING)
        monkeypatch.setitem(registry._cache, provider, module)

    return types.SimpleNamespace(calls=calls)


def _connection(provider, config=None, slug="default"):
    from backend.models import Connection, db

    row = Connection(family="social", provider=provider, account_slug=slug, status="connected",
                     auth_kind="api_token", config=json.dumps(config or {}))
    db.session.add(row)
    db.session.commit()
    return row


def _records(result):
    from backend.models import PublishRecord

    return [PublishRecord.query.get(q["publish_record_id"]) for q in result["queued"]]


def _send(record):
    """Approve the record and run its job; returns the request the provider got."""
    from backend.models import Task

    out = publish_service.approve(record)
    task = Task.query.get(out["task_id"])
    publish_runner.run({"workflow_config": task.workflow_config}, lambda *a: None)
    return record


MASTODON_CONFIG = {"instance_url": "https://m.example"}


# --- a target with no visibility chosen ------------------------------------------
@pytest.mark.parametrize("chosen", [None, "", "   "])
def test_no_choice_is_stored_and_sent_as_the_platforms_default(db_app, platform, chosen):
    mastodon = _connection("mastodon", MASTODON_CONFIG)

    record, = _records(publish_service.queue_publish(
        connection_ids=[mastodon.id], body="hello", visibility=chosen,
    ))

    assert record.visibility == registry.spec_for("mastodon").capabilities.default_visibility == "unlisted"
    _send(record)
    assert platform.calls[-1][2].visibility == "unlisted"


def test_preflight_names_the_visibility_each_target_goes_out_with(db_app, platform):
    mastodon = _connection("mastodon", MASTODON_CONFIG)
    bluesky = _connection("bluesky")

    result = publish_service.preflight([mastodon.id, bluesky.id], [], body="hello")

    assert result["ok"] is True
    assert result["per_connection"][str(mastodon.id)]["visibility"] == "unlisted"
    assert result["per_connection"][str(bluesky.id)]["visibility"] == "public"


# --- several targets, each with its own choices ----------------------------------
def test_each_target_takes_its_own_visibility(db_app, platform):
    mastodon = _connection("mastodon", MASTODON_CONFIG)
    youtube = _connection("youtube")
    bluesky = _connection("bluesky")
    chosen = {str(mastodon.id): "private", str(youtube.id): "private", str(bluesky.id): "public"}

    preflight = publish_service.preflight(
        [mastodon.id, youtube.id, bluesky.id], [], body="hello", visibilities=chosen,
    )
    assert {cid: entry["visibility"] for cid, entry in preflight["per_connection"].items()} == chosen

    records = _records(publish_service.queue_publish(
        connection_ids=[mastodon.id, bluesky.id], body="hello", visibilities=chosen,
    ))
    assert [r.visibility for r in records] == ["private", "public"]


def test_a_target_left_out_of_the_map_falls_back_to_visibility_then_its_default(db_app, platform):
    mastodon = _connection("mastodon", MASTODON_CONFIG)
    other = _connection("mastodon", MASTODON_CONFIG, slug="second")
    bluesky = _connection("bluesky")

    records = _records(publish_service.queue_publish(
        connection_ids=[mastodon.id, other.id], body="hello",
        visibility="private", visibilities={str(mastodon.id): "direct"},
    ))
    assert [r.visibility for r in records] == ["direct", "private"]

    record, = _records(publish_service.queue_publish(
        connection_ids=[bluesky.id], body="hello", visibilities={str(mastodon.id): "direct"},
    ))
    assert record.visibility == "public"


def test_a_visibility_a_target_does_not_offer_refuses_the_whole_post(db_app, platform):
    from backend.models import PublishRecord

    mastodon = _connection("mastodon", MASTODON_CONFIG)
    bluesky = _connection("bluesky")

    with pytest.raises(ValueError, match="Bluesky: Visibility 'unlisted' is not supported"):
        publish_service.queue_publish(
            connection_ids=[mastodon.id, bluesky.id], body="hello",
            visibilities={str(mastodon.id): "unlisted", str(bluesky.id): "unlisted"},
        )
    assert PublishRecord.query.count() == 0


# --- editing a held publish ----------------------------------------------------------
def test_clearing_the_visibility_on_edit_stores_the_default(db_app, platform):
    mastodon = _connection("mastodon", MASTODON_CONFIG)
    record, = _records(publish_service.queue_publish(
        connection_ids=[mastodon.id], body="hello", visibility="private",
    ))

    report = publish_service.edit_preflight(record, publish_service.edit_changes({"visibility": None}))
    assert next(iter(report["per_connection"].values()))["visibility"] == "unlisted"
    out = publish_service.edit(record, publish_service.edit_changes({"visibility": None}))

    assert out["changed"] == ["visibility"] and record.visibility == "unlisted"


# --- the routes ------------------------------------------------------------------------
def test_routes_take_a_visibility_per_target(db_app, platform):
    from backend.api.connections_api import connections_bp

    db_app.register_blueprint(connections_bp)
    client = db_app.test_client()
    mastodon = _connection("mastodon", MASTODON_CONFIG)
    bluesky = _connection("bluesky")
    payload = {
        "connection_ids": [mastodon.id, bluesky.id], "body": "x",
        "visibilities": {str(mastodon.id): "public", str(bluesky.id): "public"},
    }

    preflight = client.post("/api/connections/publish/preflight", json=payload).get_json()
    assert preflight["per_connection"][str(mastodon.id)]["visibility"] == "public"
    assert client.post("/api/connections/publish", json=payload).status_code == 202
    rows = client.get(f"/api/connections/publishes?connection_id={mastodon.id}").get_json()["publishes"]
    assert [r["visibility"] for r in rows] == ["public"]


# --- the declarations ---------------------------------------------------------------
@pytest.mark.parametrize("provider", sorted(registry.PROVIDER_MODULES))
def test_every_default_is_one_of_the_choices(provider):
    caps = registry.spec_for(provider).capabilities
    assert caps.default_visibility in caps.visibilities


def test_the_contract_flags_a_default_that_is_not_offered(monkeypatch):
    from backend.services.connections.base import Capabilities, ProviderSpec, RateLimit

    spec = ProviderSpec(
        provider="fake", family="social", label="Fake", auth_kinds=("api_token",),
        capabilities=Capabilities(
            visibilities=("public",), default_visibility="private",
            rate_limits=(RateLimit(posts=1, window_seconds=60, platform_limit="x", source="https://x"),),
        ),
    )
    module = types.SimpleNamespace(SPEC=spec, test=lambda ctx: None, publish=lambda *a: None)
    monkeypatch.setitem(registry._cache, "fake", module)
    assert registry.contract_problems("fake") == ["default_visibility must be one of visibilities."]


# --- YouTube's public opt-in -------------------------------------------------------
YOUTUBE_OPT_IN = registry.spec_for("youtube").capabilities.public_opt_in


def _video(tmp_path, name="clip.mp4"):
    from backend.models import Document, db

    path = tmp_path / name
    path.write_bytes(b"\0" * 100)
    doc = Document(filename=name, path=str(path), type=path.suffix)
    db.session.add(doc)
    db.session.commit()
    return doc


def _setting(key, value):
    from backend.models import Setting, db

    db.session.add(Setting(key=key, value=value))
    db.session.commit()


def test_youtube_holds_back_unlisted_and_public_behind_its_switch():
    assert YOUTUBE_OPT_IN.visibilities == ("unlisted", "public")
    assert YOUTUBE_OPT_IN.legacy_setting == "youtube_allow_public_publish"
    assert "Connections page" in YOUTUBE_OPT_IN.refusal
    assert "approval" in YOUTUBE_OPT_IN.explanation
    assert [url for _title, url in YOUTUBE_OPT_IN.sources] == [
        "https://support.google.com/youtube/answer/14328491",
        "https://developers.google.com/youtube/terms/developer-policies",
        "https://developers.google.com/youtube/v3/docs/videos",
    ]
    others = [p for p in registry.PROVIDER_MODULES if registry.spec_for(p).capabilities.public_opt_in]
    assert others == ["youtube"]
    serialised = registry.spec_for("youtube").to_dict()["capabilities"]["public_opt_in"]
    assert serialised["label"] == "Allow public uploads" and serialised["sources"][0]["title"]


@pytest.mark.parametrize("config, legacy, expected", [
    ({}, None, {"on": False, "source": "default"}),
    ({"allow_public": True}, None, {"on": True, "source": "connection"}),
    ({}, "true", {"on": True, "source": "setting"}),
    ({"allow_public": False}, "true", {"on": False, "source": "connection"}),
    ({"allow_public": "true"}, None, {"on": False, "source": "default"}),
    ("not json", None, {"on": False, "source": "default"}),
])
def test_the_switch_wins_and_the_old_setting_fills_in(db_app, config, legacy, expected):
    from backend.services.connections import gates

    if legacy is not None:
        _setting("youtube_allow_public_publish", legacy)
    stored = config if isinstance(config, str) else json.dumps(config)

    assert gates.public_opt_in_state("youtube", stored) == expected
    assert gates.public_opt_in_state("bluesky", stored) is None


def test_the_connection_reports_its_state(db_app):
    youtube = _connection("youtube")
    bluesky = _connection("bluesky")

    assert youtube.to_dict()["public_opt_in"] == {"on": False, "source": "default"}
    assert bluesky.to_dict()["public_opt_in"] is None


def test_public_is_refused_at_queue_time_while_off_and_private_still_goes(db_app, platform, tmp_path):
    from backend.models import PublishRecord

    youtube = _connection("youtube")
    clip = _video(tmp_path)
    when = datetime.now() + LATER

    for visibility in ("public", "unlisted"):
        pre = publish_service.preflight([youtube.id], [clip.id], body="b", visibility=visibility)
        assert pre["per_connection"][str(youtube.id)]["violations"] == [YOUTUBE_OPT_IN.refusal]
        with pytest.raises(ValueError, match="YouTube: Public and unlisted uploads are off"):
            publish_service.queue_publish(connection_ids=[youtube.id], document_ids=[clip.id],
                                          body="b", visibility=visibility)
    # A scheduled public release is refused the same way, before any record.
    pre = publish_service.preflight([youtube.id], [clip.id], body="b", visibility="public", scheduled_at=when)
    assert pre["per_connection"][str(youtube.id)]["violations"] == [YOUTUBE_OPT_IN.refusal]
    with pytest.raises(ValueError, match="Public and unlisted uploads are off"):
        publish_service.queue_publish(connection_ids=[youtube.id], document_ids=[clip.id], body="b",
                                      visibility="public", scheduled_at=when)
    assert PublishRecord.query.count() == 0

    # Private, scheduled: Guaardvark's timer uploads it privately at its time.
    pre = publish_service.preflight([youtube.id], [clip.id], body="b", scheduled_at=when)
    entry = pre["per_connection"][str(youtube.id)]
    assert entry["violations"] == [] and entry["visibility"] == "private"
    assert entry["schedule"]["mode"] == "timer"


def test_switched_on_a_scheduled_public_upload_is_handed_to_youtube(db_app, platform, tmp_path):
    youtube = _connection("youtube", {"allow_public": True})
    clip = _video(tmp_path)
    when = datetime.now() + LATER

    record, = _records(publish_service.queue_publish(
        connection_ids=[youtube.id], document_ids=[clip.id], body="b",
        visibilities={str(youtube.id): "public"}, scheduled_at=when,
    ))
    assert record.visibility == "public"
    _send(record)

    kind, provider, request = platform.calls[-1]
    assert (kind, provider, request.visibility) == ("schedule", "youtube", "public")
    assert record.status == "scheduled" and record.scheduled_natively


def test_switching_off_holds_what_was_queued_public(db_app, platform, tmp_path, monkeypatch):
    from backend.models import Task, db
    from backend.services.connections import scheduler

    _no_secrets(monkeypatch)
    youtube = _connection("youtube", {"allow_public": True})
    clip = _video(tmp_path)
    now_post, = _records(publish_service.queue_publish(
        connection_ids=[youtube.id], document_ids=[clip.id], body="b", visibility="public",
    ))
    timer_post, = _records(publish_service.queue_publish(
        connection_ids=[youtube.id], document_ids=[clip.id], body="b", visibility="unlisted",
        scheduled_at=datetime.now() + LATER,
    ))
    publish_service.approve(timer_post)
    assert timer_post.status == "scheduled"
    service.update_connection(youtube, {"allow_public": False})

    # Approval: refused, and the record keeps waiting without the stamp.
    with pytest.raises(RuntimeError, match="Public and unlisted uploads are off"):
        publish_service.approve(now_post)
    assert now_post.status == "awaiting_approval" and now_post.approved_at is None

    # The timer: sent back to Approvals with the reason.
    timer_post.scheduled_at = datetime.now() - timedelta(minutes=2)
    out = scheduler.release_due(datetime.now())
    assert out["held"] == [timer_post.id]
    assert timer_post.status == "awaiting_approval" and timer_post.error_message == YOUTUBE_OPT_IN.refusal

    # A job already queued: failed by the runner, nothing sent.
    now_post.status = "queued"
    task = Task(name="Publish to YouTube", status="queued", task_handler="connections",
                workflow_config=json.dumps({"publish_record_id": now_post.id}))
    db.session.add(task)
    db.session.commit()
    out = publish_runner.run({"workflow_config": task.workflow_config}, lambda *a: None)
    assert out == {"error": YOUTUBE_OPT_IN.refusal} and now_post.status == "failed"
    assert platform.calls == []


def test_an_edit_to_public_is_refused_while_off(db_app, platform, tmp_path):
    youtube = _connection("youtube")
    record, = _records(publish_service.queue_publish(
        connection_ids=[youtube.id], document_ids=[_video(tmp_path).id], body="b",
    ))

    report = publish_service.edit_preflight(record, publish_service.edit_changes({"visibility": "public"}))
    assert next(iter(report["per_connection"].values()))["violations"] == [YOUTUBE_OPT_IN.refusal]
    with pytest.raises(ValueError, match="Public and unlisted uploads are off"):
        publish_service.edit(record, publish_service.edit_changes({"visibility": "public"}))
    assert record.visibility == "private"


def test_the_youtube_provider_refuses_before_reaching_youtube(db_app, monkeypatch):
    import requests

    from backend.services.connections.providers import youtube

    def no_network(*args, **kwargs):
        raise AssertionError("YouTube was reached")

    monkeypatch.setattr(requests, "post", no_network)
    monkeypatch.setattr(requests, "put", no_network)
    ctx = base.ConnCtx(connection={}, config={}, secrets={})
    request = base.PublishRequest(record_id=1, connection_id=1, visibility="public",
                                  publish_at=datetime.now() + LATER)

    assert youtube.publish(ctx, request, lambda *a: None).message == YOUTUBE_OPT_IN.refusal
    private = base.PublishRequest(record_id=1, connection_id=1, visibility="private",
                                  publish_at=datetime.now() + LATER)
    assert youtube.schedule(ctx, private, lambda *a: None).message == YOUTUBE_OPT_IN.refusal


# --- the switch on the connection ---------------------------------------------------
def _no_secrets(monkeypatch):
    monkeypatch.setattr(service.credential_store, "apply_secret_updates",
                        lambda *a, **k: {"updated": [], "skipped_empty": False})


def test_the_switch_is_stored_on_the_connection(db_app, monkeypatch):
    _no_secrets(monkeypatch)
    youtube = _connection("youtube", {"channel_id": "c"})

    service.update_connection(youtube, {"allow_public": True})
    assert json.loads(youtube.config) == {"channel_id": "c", "allow_public": True}
    assert youtube.to_dict()["public_opt_in"] == {"on": True, "source": "connection"}

    service.update_connection(youtube, {"allow_public": False})
    assert youtube.to_dict()["public_opt_in"] == {"on": False, "source": "connection"}


@pytest.mark.parametrize("value", ["true", 1, None])
def test_the_switch_takes_only_a_boolean(db_app, monkeypatch, value):
    _no_secrets(monkeypatch)
    youtube = _connection("youtube")

    with pytest.raises(ValueError, match="true or false"):
        service.update_connection(youtube, {"allow_public": value})


def test_a_provider_without_the_opt_in_refuses_the_switch(db_app, monkeypatch):
    _no_secrets(monkeypatch)
    bluesky = _connection("bluesky")

    with pytest.raises(ValueError, match="nothing to allow"):
        service.update_connection(bluesky, {"allow_public": True})
    assert "allow_public" not in (bluesky.config or "")


def test_a_config_field_cannot_smuggle_the_switch_in(db_app, monkeypatch):
    _no_secrets(monkeypatch)
    youtube = _connection("youtube")

    service.update_connection(youtube, {"config": {"allow_public": True}})

    assert youtube.to_dict()["public_opt_in"]["on"] is False


def test_the_route_switches_it(db_app, monkeypatch):
    from backend.api.connections_api import connections_bp

    _no_secrets(monkeypatch)
    db_app.register_blueprint(connections_bp)
    client = db_app.test_client()
    youtube = _connection("youtube")

    saved = client.put(f"/api/connections/{youtube.id}", json={"allow_public": True})
    assert saved.status_code == 200 and saved.get_json()["public_opt_in"]["on"] is True
    listed = client.get("/api/connections?family=social").get_json()["connections"]
    assert listed[0]["public_opt_in"] == {"on": True, "source": "connection"}


def test_the_contract_keeps_the_default_out_of_the_opt_in(monkeypatch):
    from backend.services.connections.base import Capabilities, ProviderSpec, PublicOptIn, RateLimit

    spec = ProviderSpec(
        provider="fake", family="social", label="Fake", auth_kinds=("api_token",),
        capabilities=Capabilities(
            visibilities=("private", "public"), default_visibility="public",
            rate_limits=(RateLimit(posts=1, window_seconds=60, platform_limit="x", source="https://x"),),
            public_opt_in=PublicOptIn(visibilities=("public", "secret"), label="", explanation="", refusal=""),
        ),
    )
    module = types.SimpleNamespace(SPEC=spec, test=lambda ctx: None, publish=lambda *a: None)
    monkeypatch.setitem(registry._cache, "fake", module)
    assert registry.contract_problems("fake") == [
        "public_opt_in must not hold back default_visibility.",
        "public_opt_in names a visibility the provider does not offer.",
        "public_opt_in needs a label, an explanation and a refusal.",
    ]
