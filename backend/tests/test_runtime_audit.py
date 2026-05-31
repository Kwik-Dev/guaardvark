"""Phase 1 runtime-liveness layer tests.

No @requires_llm, no live services. Uses an in-memory sqlite app so the
flush()/prune paths exercise the portable read-modify-write upsert fallback.
"""

import pytest

try:
    from flask import Flask
    from backend.models import db, SymbolHit
    from backend.services import execution_context_tracker as ect
    from backend.services.execution_context_tracker import (
        ExecutionContextTracker,
        MODE_CELERY_TASK,
    )
    from backend.tasks.runtime_audit_tasks import create_runtime_audit_tasks
except Exception:
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


# (a) buffer aggregation -------------------------------------------------------
def test_record_hit_aggregates_count_and_ors_mode_flags():
    tracker = ExecutionContextTracker()
    tracker.record_hit("task:foo", "task", "foo", "mod", mode_bit=1)
    tracker.record_hit("task:foo", "task", "foo", "mod", mode_bit=4)

    entry = tracker._buffer["task:foo"]
    assert entry["count"] == 2
    assert entry["mode_flags"] == (1 | 4)


# (b) idempotent upsert --------------------------------------------------------
def test_flush_upserts_idempotently(app):
    with app.app_context():
        tracker = ExecutionContextTracker()
        tracker.record_hit("task:bar", "task", "bar", "mod", mode_bit=MODE_CELERY_TASK)
        tracker.record_hit("task:bar", "task", "bar", "mod", mode_bit=MODE_CELERY_TASK)
        assert tracker.flush() == 1

        tracker.record_hit("task:bar", "task", "bar", "mod", mode_bit=2)
        tracker.flush()

        rows = SymbolHit.query.all()
        assert len(rows) == 1  # one row, not two
        row = rows[0]
        assert row.symbol_id == "task:bar"
        assert row.hit_count == 3            # 2 + 1, summed across flushes
        assert row.mode_flags == (1 | 2)     # ORed across flushes


def test_flush_empty_buffer_is_noop(app):
    with app.app_context():
        tracker = ExecutionContextTracker()
        assert tracker.flush() == 0
        assert SymbolHit.query.count() == 0


# (c) prune keeps reachable-but-cold ------------------------------------------
def test_prune_deletes_stale_nonreachable_keeps_reachable(app):
    import datetime as _dt

    with app.app_context():
        old = _dt.datetime.now() - _dt.timedelta(days=200)
        fresh = _dt.datetime.now()

        # stale + not reachable -> should be deleted
        db.session.add(SymbolHit(
            symbol_id="task:stale_cold", symbol_kind="task", display_name="stale_cold",
            module="m", mode_flags=1, hit_count=1, last_fired_at=old,
            static_reachability=False,
        ))
        # stale + reachability NULL -> should be deleted
        db.session.add(SymbolHit(
            symbol_id="task:stale_null", symbol_kind="task", display_name="stale_null",
            module="m", mode_flags=1, hit_count=1, last_fired_at=old,
            static_reachability=None,
        ))
        # stale BUT reachable -> must be KEPT (once-a-month handler)
        db.session.add(SymbolHit(
            symbol_id="task:stale_reachable", symbol_kind="task", display_name="stale_reachable",
            module="m", mode_flags=1, hit_count=1, last_fired_at=old,
            static_reachability=True,
        ))
        # fresh -> kept regardless
        db.session.add(SymbolHit(
            symbol_id="task:fresh", symbol_kind="task", display_name="fresh",
            module="m", mode_flags=1, hit_count=1, last_fired_at=fresh,
            static_reachability=False,
        ))
        db.session.commit()

        # Build the tasks against a throwaway Celery app to get the closures.
        from celery import Celery
        capp = Celery("test_runtime_audit")
        create_runtime_audit_tasks(capp)
        prune = capp.tasks["runtime_audit.prune_old_hits"]

        result = prune.run()
        assert result["status"] == "ok"
        assert result["deleted"] == 2

        remaining = {r.symbol_id for r in SymbolHit.query.all()}
        assert remaining == {"task:stale_reachable", "task:fresh"}


# (d) tracker never raises if the DB is unavailable ----------------------------
def test_tracker_swallows_db_errors(app, monkeypatch):
    with app.app_context():
        tracker = ExecutionContextTracker()
        tracker.record_hit("task:boom", "task", "boom", "mod", mode_bit=1)

        # Force the flush DB path to blow up.
        def _boom(*a, **k):
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(db.session, "execute", _boom)
        monkeypatch.setattr(db.session, "get", _boom)
        monkeypatch.setattr(db.session, "add", _boom)

        # Must not raise, and must report 0 flushed.
        assert tracker.flush() == 0
        # The buffered hit was merged back, not lost.
        assert "task:boom" in tracker._buffer


def test_record_hit_never_raises_on_bad_internal_state():
    tracker = ExecutionContextTracker()
    # Corrupt the lock so the with-block raises inside record_hit; it must
    # still swallow rather than propagate.
    tracker._lock = None
    tracker.record_hit("task:x", "task", "x", "m", mode_bit=1)  # no exception
