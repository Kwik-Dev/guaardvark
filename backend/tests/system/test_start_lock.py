"""start.sh overlap lock: stop.sh must reap abandoned/stopped installers."""
from __future__ import annotations

import os
import signal
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
LOCK_SH = REPO / "scripts" / "lib" / "start_lock.sh"


def _bash(script: str, cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(cwd or REPO),
        env=merged,
        text=True,
        capture_output=True,
        timeout=15,
    )


def _spawn_named_start(tmp_path: Path) -> tuple[subprocess.Popen, int]:
    """Spawn `/bin/bash ./start.sh` from a fake install root (matches production argv)."""
    (tmp_path / "start.sh").write_text("#!/bin/bash\nwhile true; do sleep 60; done\n")
    (tmp_path / "start.sh").chmod(0o755)
    proc = subprocess.Popen(
        ["/bin/bash", "./start.sh"],
        cwd=str(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + 3
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            os.kill(proc.pid, 0)
            cmd = subprocess.check_output(["ps", "-p", str(proc.pid), "-o", "command="], text=True)
            if "start.sh" in cmd:
                return proc, proc.pid
        except OSError:
            break
        time.sleep(0.05)
    proc.kill()
    raise AssertionError("could not spawn dummy /bin/bash ./start.sh")


@pytest.fixture
def dummy_start(tmp_path):
    holder, pid = _spawn_named_start(tmp_path)
    try:
        yield tmp_path, pid
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            holder.kill()
        except OSError:
            pass
        try:
            holder.wait(timeout=2)
        except Exception:
            pass


def test_guard_take_when_pid_dead(tmp_path):
    script = textwrap.dedent(f"""
        SCRIPT_DIR="{tmp_path}"
        . "{LOCK_SH}"
        start_lock_guard_decision 999999999
    """)
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "take"


def test_guard_take_when_unrelated_sleep(tmp_path):
    sleep = subprocess.Popen(["sleep", "30"])
    try:
        script = textwrap.dedent(f"""
            SCRIPT_DIR="{tmp_path}"
            . "{LOCK_SH}"
            start_lock_guard_decision {sleep.pid}
        """)
        r = _bash(script)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "take"
    finally:
        sleep.kill()
        sleep.wait(timeout=2)


def test_guard_live_when_runnable_start_sh(dummy_start):
    root, pid = dummy_start
    script = textwrap.dedent(f"""
        SCRIPT_DIR="{root}"
        . "{LOCK_SH}"
        start_lock_guard_decision {pid}
    """)
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "live"
    assert _bash(textwrap.dedent(f"""
        SCRIPT_DIR="{root}"
        . "{LOCK_SH}"
        start_lock_is_our_script {pid} && echo yes
    """)).stdout.strip() == "yes"


def test_guard_take_when_start_sh_is_stopped(dummy_start):
    root, pid = dummy_start
    os.kill(pid, signal.SIGSTOP)
    time.sleep(0.05)
    script = textwrap.dedent(f"""
        SCRIPT_DIR="{root}"
        . "{LOCK_SH}"
        start_lock_guard_decision {pid}
    """)
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "take"


def test_reap_from_child_does_not_kill_parent_start_sh(tmp_path):
    """start.sh step 1 runs stop.sh — that child must not reap its parent."""
    (tmp_path / "start.sh").write_text(
        textwrap.dedent(f"""\
            #!/bin/bash
            SCRIPT_DIR="{tmp_path}"
            . "{LOCK_SH}"
            START_LOCK_PROTECT_PIDS="$$"
            export START_LOCK_PROTECT_PIDS
            bash -c 'SCRIPT_DIR="{tmp_path}"; . "{LOCK_SH}"; start_lock_reap'
            echo SURVIVED
        """)
    )
    (tmp_path / "start.sh").chmod(0o755)
    r = subprocess.run(
        ["/bin/bash", "./start.sh"],
        cwd=str(tmp_path),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert r.returncode == 0, r.stderr
    assert "SURVIVED" in r.stdout


def test_reap_kills_stopped_start_sh(dummy_start):
    root, pid = dummy_start
    os.kill(pid, signal.SIGSTOP)
    pidfile = root / "start.sh.pid"
    pidfile.write_text(str(pid))
    script = textwrap.dedent(f"""
        SCRIPT_DIR="{root}"
        START_PID_FILE="{pidfile}"
        . "{LOCK_SH}"
        start_lock_reap
    """)
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "Stopped leftover start.sh" in r.stdout
    time.sleep(0.2)
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.exists():
        # Parent may not have wait()'d yet — a zombie is "dead enough".
        state = stat_path.read_text().split()[2]
        assert state == "Z"
    assert not pidfile.exists()
