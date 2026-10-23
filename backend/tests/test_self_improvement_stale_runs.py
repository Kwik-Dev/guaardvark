"""A self-improvement run whose worker died is closed, not shown running forever.

_fail_if_still_running only runs when the runner returns. A worker killed by
a restart leaves its row ``running``; recover_stale_runs closes such rows at
startup and from the status routes, with a reason the scan views show.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from flask import Flask

from backend.services.self_improvement_service import (
    STALE_RUN_AFTER,
    STALE_RUN_REASON,
    SelfImprovementService,
)

SVC = "backend.services.self_improvement_service"


@pytest.fixture
def app():
    from backend.models import db
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _fresh_service():
    svc = object.__new__(SelfImprovementService)
    svc._initialized = False
    svc.__init__()
    return svc


def _running_run(age=timedelta(0)):
    from backend.models import db, SelfImprovementRun
    run = SelfImprovementRun(trigger="scheduled", status="running", node_id="local",
                             timestamp=datetime.now() - age)
    db.session.add(run)
    db.session.commit()
    return run


def _status(run_id):
    from backend.models import db, SelfImprovementRun
    db.session.expire_all()
    run = db.session.get(SelfImprovementRun, run_id)
    return run.status, run.error_message


def test_no_live_worker_closes_every_running_row_with_a_reason(app):
    svc = _fresh_service()
    fresh = _running_run()
    old = _running_run(age=STALE_RUN_AFTER + timedelta(minutes=1))
    with patch.object(SelfImprovementService, "_celery_has_live_run", return_value=False):
        assert svc.recover_stale_runs() == 2
    assert _status(fresh.id) == ("failed", STALE_RUN_REASON)
    assert _status(old.id) == ("failed", STALE_RUN_REASON)


def test_live_worker_keeps_a_young_row_and_closes_an_old_one(app):
    svc = _fresh_service()
    fresh = _running_run()
    old = _running_run(age=STALE_RUN_AFTER + timedelta(minutes=1))
    with patch.object(SelfImprovementService, "_celery_has_live_run", return_value=True):
        assert svc.recover_stale_runs() == 1
    assert _status(fresh.id)[0] == "running"
    assert _status(old.id) == ("failed", STALE_RUN_REASON)


def test_inspect_unavailable_is_treated_like_a_live_worker(app):
    svc = _fresh_service()
    fresh = _running_run()
    with patch.object(SelfImprovementService, "_celery_has_live_run", return_value=None):
        assert svc.recover_stale_runs() == 0
    assert _status(fresh.id)[0] == "running"


def test_polled_routes_skip_inspect_and_close_only_old_rows(app):
    svc = _fresh_service()
    fresh = _running_run()
    old = _running_run(age=STALE_RUN_AFTER + timedelta(minutes=1))
    with patch.object(SelfImprovementService, "_celery_has_live_run") as probe:
        assert svc.recover_stale_runs(probe_workers=False) == 1
    probe.assert_not_called()
    assert _status(fresh.id)[0] == "running"
    assert _status(old.id)[0] == "failed"


def test_the_run_this_process_owns_is_left_alone(app):
    svc = _fresh_service()
    mine = _running_run(age=STALE_RUN_AFTER + timedelta(minutes=1))
    svc._current_run_id = mine.id
    with patch.object(SelfImprovementService, "_celery_has_live_run", return_value=False):
        assert svc.recover_stale_runs() == 0
    assert _status(mine.id)[0] == "running"


def test_closed_rows_are_not_touched_again(app):
    from backend.models import db
    svc = _fresh_service()
    done = _running_run()
    done.status = "success"
    db.session.commit()
    with patch.object(SelfImprovementService, "_celery_has_live_run", return_value=False):
        assert svc.recover_stale_runs() == 0
    assert _status(done.id) == ("success", None)
