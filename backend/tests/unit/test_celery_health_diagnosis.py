"""The Celery health diagnosis must not call a dead worker "busy" (2026-10-05).

`/api/health/celery` reported `down` with "Worker may be processing large tasks" even
when no worker existed at all — which is how the Oct-4 default-queue backlog stayed
invisible. The old code also gated its worker probe on `"timeout" in error_msg`, which
Celery's `TimeoutError` ("The operation timed out.") never matches.

A first fix probed *any* registered worker, so with `main` (the only consumer of the
`health` queue) dead and the idle `training` worker alive it still returned `busy`/200 —
the same failure wearing a different hat. These pin the queue-aware version: only a worker
that consumes `health` makes the endpoint busy, and "no worker" stays honest about
`--pool=solo` (a worker mid-task cannot answer the broadcast either).

Unit tests for the two helpers, plus two route-level tests with a stubbed celery. No
broker, no GPU.
"""
from __future__ import annotations

from backend.app import _celery_health_diagnosis, _celery_worker_probe


# --- _celery_health_diagnosis -------------------------------------------------
# probe shape: reachable, workers, health_workers, health_queues_read, active_tasks, queued_tasks


def test_main_dead_training_alive_is_down_not_busy():
    """The P1 regression: a worker on another queue must not read as backlog."""
    body = _celery_health_diagnosis(
        {"reachable": True, "workers": ["training@host"], "health_workers": [],
         "active_tasks": 0, "queued_tasks": 0},
        "The operation timed out.",
    )
    assert body["status"] == "down"
    assert "No Celery worker" in body["error"]
    assert "health" in body["error"]
    assert "restart_celery.sh" in body["suggestion"]


def test_health_consumer_present_is_busy():
    body = _celery_health_diagnosis(
        {"reachable": True, "workers": ["main@host", "training@host"],
         "health_workers": ["main@host"], "active_tasks": 1, "queued_tasks": 0},
        "The operation timed out.",
    )
    assert body["status"] == "busy"
    assert "backlog" in body["message"].lower()
    # A busy body carries an `error` too, or the Studio renders a literal "Service down".
    assert body["error"]
    assert "busy" in body["error"].lower()
    assert body["workers"] == ["main@host", "training@host"]


def test_no_worker_answered_is_down_with_softened_wording():
    body = _celery_health_diagnosis(
        {"reachable": True, "workers": [], "health_workers": [],
         "active_tasks": 0, "queued_tasks": 0},
        "The operation timed out.",
    )
    assert body["status"] == "down"
    assert "No Celery worker" in body["error"]
    # --pool=solo: a busy worker also cannot answer, so no unconditional claim.
    assert "none answered" in body["error"]
    assert "restart_celery.sh" in body["suggestion"]
    # The misleading old message must not come back.
    assert "processing large tasks" not in (body["error"] + body["suggestion"]).lower()


def test_unreachable_broker_is_not_blamed_on_the_worker():
    body = _celery_health_diagnosis(
        {"reachable": False, "workers": [], "health_workers": [],
         "active_tasks": 0, "queued_tasks": 0},
        "Error 111 connecting to localhost:6379. Connection refused.",
    )
    assert body["status"] == "down"
    assert "Redis" in body["suggestion"]
    # It is a reachability problem, not a missing-worker problem.
    assert "No Celery worker" not in body["error"]
    assert body["error"] == "Error 111 connecting to localhost:6379. Connection refused."


def test_counts_are_carried_through_when_a_health_worker_answered():
    body = _celery_health_diagnosis(
        {"reachable": True, "workers": ["main@host"], "health_workers": ["main@host"],
         "active_tasks": 2, "queued_tasks": 4},
        "boom",
    )
    assert body["status"] == "busy"
    assert body["active_tasks"] == 2
    assert body["queued_tasks"] == 4


def test_counts_are_zeroed_when_no_worker_answered():
    """No self-contradictory body: queued work with no consumer is not a state."""
    body = _celery_health_diagnosis(
        {"reachable": True, "workers": [], "health_workers": [],
         "active_tasks": 5, "queued_tasks": 9},
        "boom",
    )
    assert body["active_tasks"] == 0
    assert body["queued_tasks"] == 0


def test_the_queue_name_is_quoted_from_the_argument():
    body = _celery_health_diagnosis(
        {"reachable": True, "workers": ["w@h"], "health_workers": [],
         "active_tasks": 0, "queued_tasks": 0},
        "boom",
        queue="health",
    )
    assert "'health'" in body["error"]


def test_unread_queue_list_softens_the_not_consuming_claim():
    """A lost active_queues reply must not assert which queues a worker consumes."""
    body = _celery_health_diagnosis(
        {"reachable": True, "workers": ["training@host"], "health_workers": [],
         "health_queues_read": False, "active_tasks": 0, "queued_tasks": 0},
        "The operation timed out.",
    )
    assert body["status"] == "down"
    assert "No Celery worker" in body["error"]
    assert "could not be read" in body["error"]
    # The confident version must not be used when the queue list is unknown.
    assert "do not consume it" not in body["error"]


# --- _celery_worker_probe -----------------------------------------------------


class _FakeInspect:
    def __init__(self, stats, active=None, scheduled=None, reserved=None,
                 active_queues=None, active_queues_raises=False):
        self._stats = stats
        self._active = active or {}
        self._scheduled = scheduled or {}
        self._reserved = reserved or {}
        self._active_queues = active_queues or {}
        self._active_queues_raises = active_queues_raises

    def stats(self):
        return self._stats

    def active(self):
        return self._active

    def scheduled(self):
        return self._scheduled

    def reserved(self):
        return self._reserved

    def active_queues(self):
        if self._active_queues_raises:
            raise OSError("no reply for active_queues")
        return self._active_queues


class _FakeControl:
    def __init__(self, inspect_obj=None, raises=False):
        self._inspect_obj = inspect_obj
        self._raises = raises

    def inspect(self, timeout=None):
        if self._raises:
            raise OSError("broker refused")
        return self._inspect_obj


class _FakeCelery:
    def __init__(self, control):
        self.control = control


def test_probe_survives_an_unreachable_broker():
    probe = _celery_worker_probe(_FakeCelery(_FakeControl(raises=True)))
    assert probe == {
        "reachable": False,
        "workers": [],
        "health_workers": [],
        "health_queues_read": False,
        "active_tasks": 0,
        "queued_tasks": 0,
    }


def test_probe_reads_workers_counts_and_the_health_queue():
    control = _FakeControl(_FakeInspect(
        stats={"main@host": {"total": {}}},
        active={"main@host": [{"id": "t1"}]},
        reserved={"main@host": [{"id": "t2"}, {"id": "t3"}]},
        active_queues={"main@host": [{"name": "health"}, {"name": "default"}]},
    ))
    probe = _celery_worker_probe(_FakeCelery(control))
    assert probe["reachable"] is True
    assert probe["workers"] == ["main@host"]
    assert probe["health_workers"] == ["main@host"]
    assert probe["active_tasks"] == 1
    assert probe["queued_tasks"] == 2


def test_probe_names_only_the_health_consumers():
    """main consumes health; training does not — the case that used to read as busy."""
    control = _FakeControl(_FakeInspect(
        stats={"main@host": {}, "training@host": {}},
        active_queues={
            "main@host": [{"name": "health"}, {"name": "default"}],
            "training@host": [{"name": "training"}],
        },
    ))
    probe = _celery_worker_probe(_FakeCelery(control))
    assert probe["workers"] == ["main@host", "training@host"]
    assert probe["health_workers"] == ["main@host"]


def test_probe_with_no_workers_is_reachable_and_empty():
    """Empty stats without an exception means the broker answered: no worker, not unknown."""
    probe = _celery_worker_probe(_FakeCelery(_FakeControl(_FakeInspect(stats={}))))
    assert probe["reachable"] is True
    assert probe["workers"] == []
    assert probe["health_workers"] == []


def test_probe_tolerates_a_failed_active_queues_reply():
    """A partial reply must not mark the broker unreachable, nor invent health consumers."""
    control = _FakeControl(_FakeInspect(
        stats={"main@host": {}},
        active_queues_raises=True,
    ))
    probe = _celery_worker_probe(_FakeCelery(control))
    assert probe["reachable"] is True
    assert probe["workers"] == ["main@host"]
    assert probe["health_workers"] == []
    # The queue list could not be read: the diagnosis softens rather than asserts.
    assert probe["health_queues_read"] is False


def test_probe_zeroes_counts_when_no_worker_answered():
    """Partial replies cannot leave a body claiming queued work with no consumer."""
    control = _FakeControl(_FakeInspect(
        stats={},
        reserved={"main@host": [{"id": "t1"}]},
    ))
    probe = _celery_worker_probe(_FakeCelery(control))
    assert probe["workers"] == []
    assert probe["active_tasks"] == 0
    assert probe["queued_tasks"] == 0


def test_the_old_string_matching_helper_is_gone():
    import backend.app as app_mod
    assert not hasattr(app_mod, "_celery_workers_registered")


# --- the route itself ---------------------------------------------------------


class _RaisingCelery:
    """A celery whose health ping never answers (Celery's real TimeoutError)."""

    def send_task(self, *args, **kwargs):
        from celery.exceptions import TimeoutError as CeleryTimeoutError
        raise CeleryTimeoutError("The operation timed out.")


def _client(monkeypatch, probe):
    import backend.app as app_mod

    monkeypatch.setattr("backend.celery_app.celery", _RaisingCelery())
    monkeypatch.setattr(app_mod, "_celery_worker_probe", lambda celery, timeout=2.0: probe)
    monkeypatch.setitem(app_mod._celery_health_cache, "data", None)
    monkeypatch.setitem(app_mod._celery_health_cache, "timestamp", 0)
    return app_mod.app.test_client()


def test_route_reports_a_dead_health_worker_as_503(monkeypatch):
    client = _client(monkeypatch, {
        "reachable": True, "workers": ["training@host"], "health_workers": [],
        "active_tasks": 0, "queued_tasks": 0,
    })
    res = client.get("/api/health/celery")
    assert res.status_code == 503
    body = res.get_json()
    assert body["status"] == "down"
    assert "No Celery worker" in body["error"]


def test_route_reports_a_busy_health_worker_as_200(monkeypatch):
    client = _client(monkeypatch, {
        "reachable": True, "workers": ["main@host"], "health_workers": ["main@host"],
        "active_tasks": 1, "queued_tasks": 0,
    })
    res = client.get("/api/health/celery")
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "busy"
    assert body["error"]
