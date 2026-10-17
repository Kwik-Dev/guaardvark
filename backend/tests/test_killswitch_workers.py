"""killswitch.sh stops this checkout's Celery workers, and only those.

Workers are recognised by the program they run and by their working
directory (scripts/lib/celery_workers.sh), never by a product name in the
command line, which a checkout's paths need not contain.
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.services import research_worker as rw

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "scripts" / "lib" / "celery_workers.sh"


def _pattern() -> re.Pattern:
    m = re.search(r"^CELERY_WORKER_CMD_PATTERN='([^']+)'$", LIB.read_text(), re.MULTILINE)
    assert m, "pattern not found in celery_workers.sh"
    return re.compile(m.group(1))


def test_killswitch_finds_workers_through_the_library_and_escalates():
    text = (ROOT / "killswitch.sh").read_text()
    step = text[text.index("[3/5]"):text.index("[4/5]")]
    assert '. "$SCRIPT_DIR/scripts/lib/celery_workers.sh"' in step
    assert 'celery_worker_pids_of "$SCRIPT_DIR"' in step
    assert "pkill" not in step
    assert "kill -TERM" in step and "kill -KILL" in step


@pytest.mark.parametrize("cmdline", [
    # start_celery.sh: the venv's celery script, run through its shebang
    "/srv/app/backend/venv/bin/python3.12 /srv/app/backend/venv/bin/celery -A backend.celery_app.celery "
    "worker --hostname=main@%h --queues=health,default,indexing,generation,training_gpu",
    # docker-compose.yml
    "/usr/local/bin/python3.12 /usr/local/bin/celery -A backend.celery_app.celery worker --hostname=training@%h",
    "celery -A backend.celery_app.celery worker --loglevel=info",
    # Celery's titles when setproctitle is installed
    "[celeryd: main@host:MainProcess] -active- (-A backend.celery_app.celery worker --hostname=main@%h)",
    "[celeryd: main@host:ForkPoolWorker-1]",
])
def test_the_pattern_matches_every_way_a_worker_is_started(cmdline):
    assert _pattern().search(cmdline), cmdline


def test_the_pattern_matches_the_research_worker(tmp_path):
    argv = rw.worker_argv(tmp_path)
    argv[0] = "/srv/app/backend/venv/bin/python"
    assert _pattern().search(" ".join(argv))


@pytest.mark.parametrize("cmdline", [
    "bash -c pgrep -f 'celery -A backend.celery_app.celery worker'",
    "/srv/app/backend/venv/bin/python3.12 /srv/app/backend/venv/bin/celery -A backend.celery_app.celery beat",
    "grep celery -A backend.celery_app.celery worker",
    "python3 -c print('celery -A backend.celery_app.celery worker')",
    "vim backend/celery_app.py",
])
def test_the_pattern_ignores_what_only_mentions_a_worker(cmdline):
    assert not _pattern().search(cmdline), cmdline


@pytest.mark.skipif(not (shutil.which("pgrep") and os.path.isdir("/proc")),
                    reason="needs pgrep and /proc")
def test_only_workers_running_in_the_checkout_are_listed(tmp_path):
    checkout, elsewhere = tmp_path / "checkout", tmp_path / "elsewhere"
    (checkout / "backend").mkdir(parents=True)
    elsewhere.mkdir()
    fake_celery = tmp_path / "celery"
    fake_celery.write_text("import time\ntime.sleep(30)\n")
    worker_args = ["-A", "backend.celery_app.celery", "worker", "--hostname=main@%h"]
    procs = {
        "ours": subprocess.Popen([sys.executable, str(fake_celery), *worker_args], cwd=checkout),
        "ours_in_a_subdir": subprocess.Popen([sys.executable, str(fake_celery), *worker_args],
                                             cwd=checkout / "backend"),
        "another_install": subprocess.Popen([sys.executable, str(fake_celery), *worker_args],
                                            cwd=elsewhere),
        # `read` is a builtin, so no child outlives the kill below.
        "a_shell_mentioning_it": subprocess.Popen(
            ["bash", "-c", "read -t 30 _; : celery -A backend.celery_app.celery worker"],
            cwd=checkout, stdin=subprocess.PIPE),
    }
    try:
        time.sleep(0.5)
        out = subprocess.run(
            ["bash", "-c", '. "$1"; celery_worker_pids_of "$2"', "_", str(LIB), str(checkout)],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout.split()
        listed = {name for name, p in procs.items() if str(p.pid) in out}
        assert listed == {"ours", "ours_in_a_subdir"}
    finally:
        for p in procs.values():
            p.kill()
            p.wait()
