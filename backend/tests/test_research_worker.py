"""The research worker: autoresearch runs off the main worker, started on demand.

Covers the route, the launch (environment, single start, failure), the idle
exit, and the start/stop scripts' view of the worker set.
"""
import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.services import research_worker as rw

REPO = Path(__file__).resolve().parents[2]


def _queue_of(task_name: str) -> str:
    from backend.celery_app import celery

    queue = celery.amqp.router.route({}, task_name)["queue"]
    return queue.name if hasattr(queue, "name") else str(queue)


# ---- routing -----------------------------------------------------------------

def test_a_research_run_goes_to_the_research_queue():
    assert _queue_of("autoresearch.execute_run") == rw.QUEUE == "research"


def test_the_short_autoresearch_checks_stay_on_main():
    assert _queue_of("autoresearch.check_idle") == "default"
    assert _queue_of("autoresearch.on_index_complete") == "default"


def test_every_routed_queue_has_a_worker():
    """A queue no worker consumes swallows its tasks without a word."""
    from backend.celery_app import celery

    text = (REPO / "start_celery.sh").read_text()
    consumed = {rw.QUEUE}
    for queues in re.findall(r'^start_worker "[a-z_]+" "([a-z_,]+)"', text, re.MULTILINE):
        consumed.update(queues.split(","))
    routed = {route["queue"] for route in celery.conf.task_routes.values()}
    assert routed <= consumed, f"routed but never consumed: {sorted(routed - consumed)}"


# ---- launch ------------------------------------------------------------------

def test_the_worker_command_names_its_queue_node_and_log(tmp_path):
    argv = rw.worker_argv(tmp_path)
    cmd = " ".join(argv)
    assert "--queues=research" in argv and "--hostname=research@%h" in argv
    assert "--pool=solo" in argv and "--concurrency=1" in argv
    assert f"--logfile={tmp_path / 'logs' / 'celery_research.log'}" in argv
    # stop.sh and start.sh find the checkout's Celery processes by these patterns.
    assert re.search(r"celery.*(worker|beat)", cmd)
    assert "celery -A backend.celery_app.celery worker" in cmd


def test_the_worker_environment_hides_the_gpu_and_marks_the_worker(tmp_path):
    env = rw.worker_env(tmp_path, base={
        "PATH": "/usr/bin", "PYTHONPATH": "/elsewhere",
        "WERKZEUG_SERVER_FD": "3", "GUAARDVARK_MCP_PROCESS": "1",
        "DATABASE_URL": "postgresql://example/db",
    })
    assert env[rw.ENV_MARKER] == "1"
    assert env["CELERY_WORKER_MODE"] == "true"
    assert env["CUDA_VISIBLE_DEVICES"] == ""
    assert env["GUAARDVARK_ROOT"] == str(tmp_path)
    assert env["PYTHONPATH"].split(os.pathsep) == [str(tmp_path), "/elsewhere"]
    assert env["DATABASE_URL"] == "postgresql://example/db"
    assert "WERKZEUG_SERVER_FD" not in env and "GUAARDVARK_MCP_PROCESS" not in env
    assert env["GUAARDVARK_OOM_SCORE_ADJ"] == str(rw.OOM_SCORE_ADJ)


@pytest.mark.parametrize("given, expected", [("0", "0"), ("900", "900"), ("300", "800"), ("x", "800")])
def test_the_oom_adjustment_only_goes_up_and_stays_off_when_switched_off(tmp_path, given, expected):
    env = rw.worker_env(tmp_path, base={"GUAARDVARK_OOM_SCORE_ADJ": given})
    assert env["GUAARDVARK_OOM_SCORE_ADJ"] == expected


def _fake_spawn(root, started):
    def spawn(r):
        pid = 4242 + len(started)
        started.append(pid)
        (r / "pids").mkdir(parents=True, exist_ok=True)
        (r / "pids" / rw.PID_NAME).write_text(f"{pid}\n")
        return pid
    return spawn


def test_dispatch_starts_the_worker_once_and_sends_each_time(tmp_path):
    started, sent = [], []
    with patch.object(rw, "_spawn", side_effect=_fake_spawn(tmp_path, started)), \
         patch.object(rw, "_is_research_worker", side_effect=lambda pid: pid in started):
        rw.dispatch(lambda: sent.append(1), root=tmp_path)
        rw.dispatch(lambda: sent.append(2), root=tmp_path)
    assert started == [4242]
    assert sent == [1, 2]


def test_a_dead_worker_is_started_again(tmp_path):
    started = []
    with patch.object(rw, "_spawn", side_effect=_fake_spawn(tmp_path, started)), \
         patch.object(rw, "_is_research_worker", return_value=False):
        rw.dispatch(lambda: None, root=tmp_path)
        rw.dispatch(lambda: None, root=tmp_path)
    assert len(started) == 2


def test_nothing_is_sent_when_the_worker_cannot_start(tmp_path):
    send = MagicMock()
    with patch.object(rw.subprocess, "Popen", side_effect=OSError("no python")):
        with pytest.raises(rw.WorkerStartError, match="no python"):
            rw.dispatch(send, root=tmp_path)
    send.assert_not_called()
    assert not (tmp_path / "pids" / rw.PID_NAME).exists()


def test_spawn_records_the_pid_and_runs_detached_in_the_checkout(tmp_path):
    proc = MagicMock(pid=777)
    proc.wait.return_value = 0
    with patch.object(rw.subprocess, "Popen", return_value=proc) as popen:
        assert rw._spawn(tmp_path) == 777
    kwargs = popen.call_args.kwargs
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["start_new_session"] is True
    assert kwargs["env"][rw.ENV_MARKER] == "1"
    assert (tmp_path / "pids" / rw.PID_NAME).read_text().strip() == "777"


def test_a_pid_file_naming_another_process_is_not_the_worker(tmp_path):
    (tmp_path / "pids").mkdir()
    (tmp_path / "pids" / rw.PID_NAME).write_text(f"{os.getpid()}\n")
    # This test process is not a celery worker on the research queue.
    assert rw.running_pid(tmp_path) is None


# ---- idle exit -----------------------------------------------------------------

class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _watch(tmp_path, *, requests=lambda: False, queued=lambda: 0, idle_exit_s=120.0):
    clock, stops = _Clock(), []
    watch = rw.IdleWatch(MagicMock(), tmp_path, idle_exit_s=idle_exit_s, clock=clock,
                         has_requests=requests, queue_length=queued,
                         stop=lambda: stops.append(clock.now))
    return watch, clock, stops


def test_the_worker_stops_after_the_idle_time_and_drops_its_pid_file(tmp_path):
    (tmp_path / "pids").mkdir()
    pid_file = tmp_path / "pids" / rw.PID_NAME
    pid_file.write_text(f"{os.getpid()}\n")
    watch, clock, stops = _watch(tmp_path)
    assert watch.check() is False          # first idle look starts the timer
    clock.now += 119
    assert watch.check() is False
    clock.now += 1
    assert watch.check() is True
    assert stops == [clock.now]
    assert not pid_file.exists()


def test_a_running_task_keeps_the_worker(tmp_path):
    busy = {"v": True}
    watch, clock, stops = _watch(tmp_path, requests=lambda: busy["v"])
    for _ in range(5):
        assert watch.check() is False
        clock.now += 600
    busy["v"] = False
    assert watch.check() is False
    clock.now += 120
    assert watch.check() is True
    assert len(stops) == 1


def test_a_queued_message_keeps_the_worker_and_restarts_the_timer(tmp_path):
    waiting = {"n": 1}
    watch, clock, stops = _watch(tmp_path, queued=lambda: waiting["n"])
    watch.check()
    clock.now += 200
    assert watch.check() is False
    waiting["n"] = 0
    clock.now += 1
    assert watch.check() is False          # timer restarted by the queued message
    clock.now += 120
    assert watch.check() is True
    assert len(stops) == 1


def test_an_unreadable_queue_keeps_the_worker(tmp_path):
    def broken():
        raise ConnectionError("redis down")
    watch, clock, stops = _watch(tmp_path, queued=broken)
    watch.check()
    clock.now += 500
    assert watch.check() is False
    assert stops == []


def test_install_does_nothing_outside_the_research_worker(monkeypatch):
    from celery.signals import worker_init, worker_ready, worker_shutdown

    monkeypatch.delenv(rw.ENV_MARKER, raising=False)
    before = [len(s.receivers) for s in (worker_init, worker_ready, worker_shutdown)]
    rw.install(MagicMock())
    assert [len(s.receivers) for s in (worker_init, worker_ready, worker_shutdown)] == before


# ---- the scripts' view of the worker set ------------------------------------------

def test_start_celery_does_not_count_the_research_worker_as_already_running():
    text = (REPO / "start_celery.sh").read_text()
    body = text[text.index("own_started_worker_pids() {"):text.index("check_workers() {")]
    assert "--queues=research" in body and "continue" in body
    assert "own_started_worker_pids | wc -l" in text[text.index("check_workers() {"):]


def test_start_sh_does_not_record_the_research_worker_as_celery_pid():
    text = (REPO / "start.sh").read_text()
    loop = text[text.index('for _pid in $(pgrep -f "celery -A backend.celery_app.celery worker"'):]
    loop = loop[:loop.index("done")]
    assert "--queues=research" in loop and "continue" in loop


def test_the_research_log_is_served_by_read_logs():
    from backend.tools import workstation_tools as wt

    assert wt._LOG_WRITERS[rw.LOG_NAME] == "backend/services/research_worker.py"
