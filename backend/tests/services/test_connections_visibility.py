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
