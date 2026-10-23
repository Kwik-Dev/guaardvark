"""Docker runs the same Celery workers as a native install.

A queue no worker consumes swallows its tasks without a word, so the backend
container must start every worker start_celery.sh starts, with the same queues
and in worker mode. The research worker is started on demand in either case.
"""
import re
import shlex
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKER = re.compile(
    r"^(?P<env>(?:[A-Z_]+=\S+ )*)celery -A backend\.celery_app\.celery worker (?P<args>.*?) &$",
    re.MULTILINE,
)


def _native_workers() -> dict:
    text = (ROOT / "start_celery.sh").read_text()
    return {
        name: set(queues.split(","))
        for name, queues in re.findall(r'^start_worker "([a-z_]+)" "([a-z_,]+)"', text, re.MULTILINE)
    }


def _docker_workers() -> dict:
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    argv = shlex.split(services["backend"]["command"])
    assert argv[:2] == ["sh", "-c"]
    workers = {}
    for m in WORKER.finditer("\n".join(line.strip() for line in argv[2].splitlines())):
        args = dict(a.split("=", 1) for a in m.group("args").split() if a.startswith("--") and "=" in a)
        node = args["--hostname"].split("@", 1)[0]
        workers[node] = {"queues": set(args["--queues"].split(",")), "env": m.group("env").split()}
    return workers


def test_docker_starts_every_native_worker_with_its_queues():
    native, docker = _native_workers(), _docker_workers()
    assert native, "start_celery.sh starts no worker this test can read"
    assert {name: w["queues"] for name, w in docker.items()} == native


def test_docker_workers_run_in_worker_mode():
    """Without it a worker also runs the server's start-up work and can use the GPU."""
    for name, worker in _docker_workers().items():
        assert "CELERY_WORKER_MODE=true" in worker["env"], name


def test_the_research_queue_is_left_to_its_on_demand_worker():
    from backend.services.research_worker import QUEUE

    for name, worker in _docker_workers().items():
        assert QUEUE not in worker["queues"], name
