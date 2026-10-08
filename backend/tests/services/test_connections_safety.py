"""Publish gates, secret isolation and job-registry wiring."""

import importlib
import sys
import types

import pytest

from backend.services.connections import gates
from backend.services.social_outreach import kill_switch as ks


@pytest.fixture
def settings(monkeypatch):
    """In-memory stand-in for the Setting table, for the publish gates and for
    the posting stop they read through the outreach kill switch."""
    store = {}
    monkeypatch.setattr(gates, "_setting", lambda key, default: store.get(key, default))
    monkeypatch.setattr(ks, "_lookup_setting", store.get)
    return store


@pytest.fixture
def db_app():
    """Flask app on an in-memory database, for the paths that read and write rows."""
    from flask import Flask

    from backend.models import db

    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def fake_celery(monkeypatch):
    """A Celery stand-in that records revokes instead of reaching a broker."""
    revoked = []
    control = types.SimpleNamespace(revoke=lambda task_id, **kwargs: revoked.append(task_id))
    module = types.ModuleType("backend.celery_app")
    module.celery = types.SimpleNamespace(control=control)
    monkeypatch.setitem(sys.modules, "backend.celery_app", module)
    return revoked


# --- gates -------------------------------------------------------------------
def test_publishing_is_enabled_by_default(settings):
    assert gates.publish_enabled() is True


def test_publishing_can_be_disabled(settings):
    settings[gates.PUBLISH_ENABLED_KEY] = "false"
    assert gates.publish_enabled() is False


def test_supervision_is_on_by_default_for_the_ui(settings):
    assert gates.publish_supervised() is True
    assert gates.requires_approval("ui") is True


def test_a_saved_supervision_off_is_honoured(settings):
    settings[gates.PUBLISH_SUPERVISED_KEY] = "false"
    assert gates.publish_supervised() is False
    assert gates.requires_approval("ui") is False


@pytest.mark.parametrize("source", ["chat", "mcp", "schedule"])
def test_agent_initiated_publishes_always_require_approval(settings, source):
    """An agent must never publish without a human click."""
    settings[gates.PUBLISH_SUPERVISED_KEY] = "false"
    assert gates.publish_supervised() is False
    assert gates.requires_approval(source) is True


def test_supervised_mode_gates_the_ui_too(settings):
    settings[gates.PUBLISH_SUPERVISED_KEY] = "true"
    assert gates.requires_approval("ui") is True


# --- source attribution ------------------------------------------------------
# requested_by arrives in an unauthenticated request body, so it is a claim.
# Claiming an agent source only raises supervision; anything else must fail safe.
@pytest.mark.parametrize("claimed", ["ui", "chat", "mcp", "schedule"])
def test_known_sources_are_preserved(claimed):
    assert gates.normalize_source(claimed) == claimed


@pytest.mark.parametrize("claimed", [None, "", "   ", "api", "cron", "UI-ish", "🙂"])
def test_unrecognised_sources_become_unknown(claimed):
    assert gates.normalize_source(claimed) == "unknown"


def test_source_matching_ignores_case_and_padding():
    assert gates.normalize_source("  MCP  ") == "mcp"


def test_an_unattributed_publish_is_supervised(settings):
    """A caller that omits its source must not land on the unsupervised branch."""
    settings[gates.PUBLISH_SUPERVISED_KEY] = "false"
    assert gates.publish_supervised() is False
    assert gates.requires_approval(gates.normalize_source(None)) is True


def test_disabled_publishing_blocks_the_gate(settings):
    settings[gates.PUBLISH_ENABLED_KEY] = "false"
    allowed, reason = gates.check_can_publish("bluesky")
    assert allowed is False
    assert "disabled" in reason.lower()


def test_cadence_backend_failure_fails_closed(settings, monkeypatch):
    """A missing rate-limit backend must refuse the post, not wave it through."""
    import backend.services.social_outreach.kill_switch as ks

    monkeypatch.setattr(
        ks, "cadence_allows_post", lambda p: (_ for _ in ()).throw(RuntimeError("redis down"))
    )
    allowed, reason = gates.check_can_publish("bluesky")
    assert allowed is False
    assert "refusing" in reason.lower()


def test_cadence_block_is_surfaced(settings, monkeypatch):
    import backend.services.social_outreach.kill_switch as ks

    monkeypatch.setattr(ks, "cadence_allows_post", lambda p: (False, "too soon"))
    allowed, reason = gates.check_can_publish("bluesky")
    assert allowed is False
    assert reason == "too soon"


def test_cadence_pass_allows_publishing(settings, monkeypatch):
    import backend.services.social_outreach.kill_switch as ks

    monkeypatch.setattr(ks, "cadence_allows_post", lambda p: (True, None))
    assert gates.check_can_publish("bluesky") == (True, None)


# --- the stop on all public posting ---------------------------------------------
# One stop covers outreach and Connections. It is its own setting: the outreach
# on/off switch defaults off, and wiring that in would block every publish on a
# fresh install.
def test_outreach_being_off_does_not_block_publishing(settings, monkeypatch):
    monkeypatch.setattr(ks, "cadence_allows_post", lambda p: (True, None))
    assert ks.is_enabled() is False  # the stock outreach default
    settings["social_outreach_enabled"] = "false"
    assert ks.is_enabled() is False
    assert gates.check_can_publish("bluesky") == (True, None)


def test_posting_stop_blocks_the_gate(settings, monkeypatch):
    monkeypatch.setattr(ks, "cadence_allows_post", lambda p: (True, None))
    settings[ks.POSTING_STOP_KEY] = "true"
    assert gates.check_can_publish("bluesky") == (False, ks.POSTING_STOPPED_REASON)


def test_posting_stop_outranks_the_publish_switch(settings):
    settings[ks.POSTING_STOP_KEY] = "true"
    settings[gates.PUBLISH_ENABLED_KEY] = "false"
    assert gates.check_can_publish("bluesky") == (False, ks.POSTING_STOPPED_REASON)


def test_unreadable_posting_stop_fails_closed(settings, monkeypatch):
    def unreadable(key):
        raise RuntimeError("database is down")

    monkeypatch.setattr(ks, "_lookup_setting", unreadable)
    monkeypatch.setattr(ks, "cadence_allows_post", lambda p: (True, None))
    allowed, reason = gates.check_can_publish("bluesky")
    assert allowed is False
    assert "refusing" in reason.lower()


def test_posting_stop_holds_outreach_without_flipping_its_switch(settings, monkeypatch):
    monkeypatch.setattr(ks, "cadence_status", lambda: {})
    settings["social_outreach_enabled"] = "true"
    settings[ks.POSTING_STOP_KEY] = "true"
    assert ks.is_enabled() is False
    snapshot = ks.status_snapshot()
    assert snapshot["enabled"] is True
    assert snapshot["posting_stopped"] is True
    assert snapshot["posting_stop_reason"] == ks.POSTING_STOPPED_REASON


def test_queueing_is_refused_while_posting_is_stopped(settings):
    from backend.services.connections import publish_service

    settings[ks.POSTING_STOP_KEY] = "true"
    with pytest.raises(RuntimeError, match="stopped"):
        publish_service.queue_publish(connection_ids=[1], body="hello")


def test_approval_is_refused_while_posting_is_stopped(settings):
    from backend.services.connections import publish_service

    settings[ks.POSTING_STOP_KEY] = "true"
    record = _Record("awaiting_approval")
    with pytest.raises(RuntimeError, match="stopped"):
        publish_service.approve(record)
    assert record.status == "awaiting_approval"


def test_preflight_reports_the_stop(settings):
    from backend.services.connections import publish_service

    settings[ks.POSTING_STOP_KEY] = "true"
    result = publish_service.preflight([], [], body="hello")
    assert result["ok"] is False
    assert result["violations"] == [ks.POSTING_STOPPED_REASON]


def test_fresh_database_publishes_with_outreach_off(db_app, monkeypatch):
    """No settings rows at all: outreach is off, publishing is not stopped."""
    monkeypatch.setattr(ks, "cadence_allows_post", lambda p: (True, None))
    assert ks.is_enabled() is False
    assert ks.posting_stop_reason() is None
    assert gates.check_can_publish("bluesky") == (True, None)


def test_fresh_database_waits_for_approval(db_app):
    """No settings row: a publish from the UI waits on the Approvals page."""
    from backend.models import Setting, db

    assert db.session.get(Setting, gates.PUBLISH_SUPERVISED_KEY) is None
    assert gates.publish_supervised() is True
    assert gates.requires_approval("ui") is True


def test_a_saved_supervision_off_row_is_honoured(db_app):
    from backend.models import Setting, db

    gates.set_publish_supervised(False)

    assert db.session.get(Setting, gates.PUBLISH_SUPERVISED_KEY).value == "false"
    assert gates.publish_supervised() is False
    assert gates.requires_approval("ui") is False


def test_settings_route_reports_and_saves_supervision(db_app):
    """The Connections page reads and writes the switch through this route."""
    from backend.api.connections_api import connections_bp

    db_app.register_blueprint(connections_bp)
    client = db_app.test_client()

    assert client.get("/api/connections/settings").get_json()["publish_supervised"] is True
    saved = client.post("/api/connections/settings", json={"publish_supervised": False})
    assert saved.get_json()["publish_supervised"] is False
    assert client.get("/api/connections/settings").get_json()["publish_supervised"] is False
    # Changing the other switch leaves the saved choice alone.
    client.post("/api/connections/settings", json={"publish_enabled": True})
    assert client.get("/api/connections/settings").get_json()["publish_supervised"] is False


def _publish_row(status, **fields):
    from backend.models import PublishRecord, db

    row = PublishRecord(platform="bluesky", body="hello", status=status, **fields)
    db.session.add(row)
    db.session.commit()
    return row


def _no_provider(monkeypatch):
    from backend.services.connections import registry

    def refuse(*args, **kwargs):
        raise AssertionError("a provider was reached")

    monkeypatch.setattr(registry, "get_provider", refuse)


def test_runner_holds_a_publish_while_posting_is_stopped(db_app, monkeypatch):
    from backend.services.connections import publish_runner, publish_service

    _no_provider(monkeypatch)
    ks._write_setting(ks.POSTING_STOP_KEY, "true")
    row = _publish_row("queued")

    out = publish_runner.run({"workflow_config": {"publish_record_id": row.id}}, lambda *a: None)

    assert out == {"skipped": True, "held": True, "reason": ks.POSTING_STOPPED_REASON}
    assert row.status == "awaiting_approval"
    assert row.error_message == publish_service.HELD_MESSAGE


def test_runner_never_sends_a_publish_that_waits_on_a_person(db_app, monkeypatch):
    """A job queued before a hold must not send the held publish after resume."""
    from backend.services.connections import publish_runner

    _no_provider(monkeypatch)
    row = _publish_row("awaiting_approval")

    out = publish_runner.run({"workflow_config": {"publish_record_id": row.id}}, lambda *a: None)

    assert out == {"skipped": True, "status": "awaiting_approval"}
    assert row.status == "awaiting_approval"


def test_stop_all_posting_holds_queued_publishes(db_app, monkeypatch, fake_celery):
    from backend.models import Task, db
    from backend.services.connections import publish_service

    monkeypatch.setattr(
        ks, "drain_pending_outreach_tasks", lambda: {"purged": 0, "revoked": 0, "errors": []}
    )
    publish_task = Task(
        name="Publish to Bluesky", status="queued", task_handler="connections",
        handler_config={"platform": "bluesky", "celery_task_id": "celery-abc"},
    )
    outreach_task = Task(name="Outreach pass", status="queued", task_handler="social_outreach")
    db.session.add_all([publish_task, outreach_task])
    db.session.commit()
    queued = _publish_row("queued", task_id=publish_task.id)
    sending = _publish_row("processing")
    posted = _publish_row("posted")

    result = ks.stop_all_posting()

    assert result["posting_stopped"] is True
    assert result["errors"] == []
    assert (result["held_publishes"], result["in_flight_publishes"]) == (1, 1)
    assert (result["cancelled_publish_tasks"], result["revoked_publish_tasks"]) == (1, 1)
    assert result["cancelled_tasks"] == 1
    assert fake_celery == ["celery-abc"]
    assert (publish_task.status, outreach_task.status) == ("cancelled", "cancelled")
    assert queued.status == "awaiting_approval"
    assert queued.error_message == publish_service.HELD_MESSAGE
    assert (sending.status, posted.status) == ("processing", "posted")


def test_resume_lifts_the_stop_and_sends_nothing(db_app, monkeypatch):
    monkeypatch.setattr(
        ks, "drain_pending_outreach_tasks", lambda: {"purged": 0, "revoked": 0, "errors": []}
    )
    held = _publish_row("queued")
    assert ks.stop_all_posting()["posting_stopped"] is True

    assert ks.resume_posting() == {"posting_stopped": False}
    assert ks.posting_stop_reason() is None
    assert held.status == "awaiting_approval"


# --- secret isolation --------------------------------------------------------
def test_connection_secrets_never_join_the_cluster_sync_allowlist():
    """PORTABLE_ENV_KEYS syncs across nodes; per-operator tokens must not."""
    from backend.services.interconnector_file_sync_service import PORTABLE_ENV_KEYS

    from backend.services.connections import registry

    for spec in registry.list_specs():
        for field in spec.credential_fields:
            assert field.name.upper() not in PORTABLE_ENV_KEYS


def test_credential_file_lives_outside_the_repo(tmp_path, monkeypatch):
    """A release archive walks the repo; credentials must not be reachable there."""
    from pathlib import Path

    from backend.utils import credential_store

    monkeypatch.delenv("GUAARDVARK_CONFIG_DIR", raising=False)
    importlib.reload(credential_store)
    path = credential_store.credentials_path().resolve()
    # Derived, not a literal: a hardcoded clone name would make this pass
    # everywhere except the one machine it was written on.
    repo_root = Path(__file__).resolve().parents[3]
    assert repo_root not in path.parents, f"credentials inside the repo: {path}"
    assert str(path).endswith(".config/guaardvark/credentials.json")


def test_redact_strips_stored_secrets_from_error_text(tmp_path, monkeypatch):
    from backend.services.connections import service
    from backend.utils import credential_store

    monkeypatch.setenv("GUAARDVARK_CONFIG_DIR", str(tmp_path))
    importlib.reload(credential_store)
    monkeypatch.setattr(service, "credential_store", credential_store)

    credential_store.set_secret("social:x:default", {"token": "hunter2-hunter2"})
    cleaned = service.redact("Provider said: bad token hunter2-hunter2 rejected")
    assert "hunter2-hunter2" not in cleaned
    assert "••••" in cleaned


# --- job wiring --------------------------------------------------------------
def test_publish_is_a_registered_job_kind():
    from backend.services.job_cancel import CANCEL_DISPATCH
    from backend.services.job_registry import REGISTRY
    from backend.services.job_types import JobKind

    assert JobKind.PUBLISH in REGISTRY
    assert JobKind.PUBLISH in CANCEL_DISPATCH


def test_publish_has_a_collector():
    from backend.api.unified_jobs_resource_api import _COLLECTORS
    from backend.services.job_types import JobKind

    assert JobKind.PUBLISH in _COLLECTORS


def test_publish_tasks_are_excluded_from_the_generic_task_collector():
    """Otherwise every publish double-lists under both 'task' and 'publish'."""
    import inspect

    from backend.api import unified_jobs_resource_api as api

    source = inspect.getsource(api._collect_tasks)
    assert "connection_publish" in source


def test_publish_status_maps_like_a_task():
    from backend.services.job_types import JobKind, JobStatus, map_status

    assert map_status(JobKind.PUBLISH, "running") == JobStatus.RUNNING
    assert map_status(JobKind.PUBLISH, "completed") == JobStatus.COMPLETED
    assert map_status(JobKind.PUBLISH, "failed") == JobStatus.FAILED


# --- publish lifecycle -------------------------------------------------------
class _Record:
    """Minimal stand-in for PublishRecord; only status is exercised here."""

    def __init__(self, status):
        self.status = status
        self.error_message = None


@pytest.mark.parametrize("status", ["posted", "failed", "cancelled", "rejected"])
def test_terminal_records_cannot_be_cancelled(status):
    from backend.services.connections import publish_service

    with pytest.raises(ValueError):
        publish_service.cancel(_Record(status))


def test_a_publish_already_being_sent_cannot_be_cancelled():
    """Flipping a mid-flight record would report a cancellation that never happened."""
    from backend.services.connections import publish_service

    with pytest.raises(ValueError, match="already being sent"):
        publish_service.cancel(_Record("processing"))


@pytest.mark.parametrize("status", ["posted", "failed", "cancelled", "rejected", "processing"])
def test_reject_is_refused_once_the_decision_has_passed(status):
    from backend.services.connections import publish_service

    with pytest.raises(ValueError):
        publish_service.reject(_Record(status), "no")
