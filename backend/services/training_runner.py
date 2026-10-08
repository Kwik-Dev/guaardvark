"""Run the trainer (services/training/scripts/finetune_model.py) as its own
process, offline, and stop it on request.

The trainer is a child of the Celery task with its own process group, so
Cancel stops the trainer and not the worker:

- Hugging Face tokens are removed from its environment and the Hub,
  transformers and datasets are switched offline, with telemetry and
  experiment trackers off; it loads only the snapshot it is handed.
- Its working folder is the job's output folder; lines it prints starting
  with EVENT_PREFIX are JSON events (progress, dataset, eval, done), every
  other line goes to train.log there.
- It is stopped (process group, SIGTERM then SIGKILL) when ``should_stop``
  says so, at the time limit, or when the reading side ends early; on Linux
  the kernel also kills it if the task process dies.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from backend.services.training.scripts.finetune_model import EVENT_PREFIX

logger = logging.getLogger(__name__)

SCRIPT = Path(__file__).resolve().parent / "training" / "scripts" / "finetune_model.py"
LOG_NAME = "train.log"
TAIL_LINES = 40

TOKEN_VARS = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_HUB_TOKEN", "HF_API_TOKEN")
OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    # Unsloth's usage-statistics opt-out; HF_HUB_OFFLINE blocks that fetch either way.
    "UNSLOTH_DISABLE_STATISTICS": "1",
    "WANDB_MODE": "disabled",
    "WANDB_DISABLED": "true",
    "PYTHONUNBUFFERED": "1",
    # Anything else that tries the web (Unsloth's error hints fetch
    # download.pytorch.org with urllib) goes to a closed local port and fails
    # here instead of leaving the machine. Local addresses stay direct.
    "HTTP_PROXY": "http://127.0.0.1:9",
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "http_proxy": "http://127.0.0.1:9",
    "https_proxy": "http://127.0.0.1:9",
    "NO_PROXY": "localhost,127.0.0.1,::1",
    "no_proxy": "localhost,127.0.0.1,::1",
}


class TrainerFailed(RuntimeError):
    """The trainer exited without a result; ``log_tail`` is the end of its log."""

    def __init__(self, message: str, log_tail: str = ""):
        super().__init__(message)
        self.log_tail = log_tail


class TrainerStopped(RuntimeError):
    """The trainer was stopped because ``should_stop`` said so (a cancel)."""


def trainer_env(base: dict | None = None) -> dict:
    """The environment the trainer runs with: the backend's, minus Hugging
    Face tokens and a blank CUDA_VISIBLE_DEVICES, plus OFFLINE_ENV."""
    env = dict(os.environ if base is None else base)
    for name in TOKEN_VARS:
        env.pop(name, None)
    # Celery workers blank CUDA_VISIBLE_DEVICES at import (indexing_service) so
    # their own work stays on the CPU; the trainer is the GPU job and must not
    # inherit that. A device number someone chose on purpose is kept.
    if env.get("CUDA_VISIBLE_DEVICES", None) == "":
        env.pop("CUDA_VISIBLE_DEVICES")
    env.update(OFFLINE_ENV)
    return env


def _is_trainer(pid: int) -> bool:
    """True when ``pid`` is a running trainer started from SCRIPT, so a stale
    pid that now belongs to another program is never signalled."""
    try:
        import psutil
        return any(part.endswith(SCRIPT.name) for part in psutil.Process(pid).cmdline())
    except Exception:
        return False


def _signal_group(pid: int, sig: int) -> None:
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        import psutil
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except Exception:
        return True


def stop_trainer(pid: Any, grace_s: float = 10.0) -> bool:
    """Stop a trainer by pid (the job's recorded pid): SIGTERM to its process
    group, SIGKILL after ``grace_s``. Returns True when a trainer was signalled."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0 or not _is_trainer(pid):
        return False
    _signal_group(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.2)
    if _alive(pid):
        _signal_group(pid, signal.SIGKILL)
    return True


def _terminate(proc: subprocess.Popen, grace_s: float) -> None:
    if proc.poll() is not None:
        return
    _signal_group(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        _signal_group(proc.pid, signal.SIGKILL)
        proc.wait()


def run(command: str, spec: dict, *, workdir: Path,
        on_event: Callable[[dict], None] | None = None,
        on_start: Callable[[int], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        time_limit_s: float | None = None,
        poll_s: float = 2.0,
        grace_s: float = 10.0,
        python: str = sys.executable,
        script: Path = SCRIPT) -> dict:
    """Run ``finetune_model.py <command> --spec`` and return its "done" event.

    ``on_start(pid)`` is called once the process exists; ``on_event(event)``
    for each event line, in order; ``should_stop()`` is polled every
    ``poll_s`` seconds from a helper thread. Raises TrainerStopped,
    TrainerFailed (with the log tail), or whatever ``on_start`` raises."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    spec_path = workdir / f"{command}_spec.json"
    spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    tail: deque = deque(maxlen=TAIL_LINES)
    outcome: dict[str, Any] = {}

    with open(workdir / LOG_NAME, "a", encoding="utf-8") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {command} ===\n")
        log.flush()
        proc = subprocess.Popen(
            [python, str(script), command, "--spec", str(spec_path)],
            cwd=str(workdir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=trainer_env(), start_new_session=True,
        )
        finished = threading.Event()

        def watch() -> None:
            started = time.monotonic()
            while not finished.wait(poll_s):
                if proc.poll() is not None:
                    return
                try:
                    stop = bool(should_stop and should_stop())
                except Exception:
                    logger.warning("Trainer stop check failed", exc_info=True)
                    stop = False
                if stop:
                    outcome["stopped"] = True
                elif time_limit_s and time.monotonic() - started > time_limit_s:
                    outcome["timed_out"] = True
                else:
                    continue
                _terminate(proc, grace_s)
                return

        watcher = threading.Thread(target=watch, daemon=True, name=f"trainer-watch-{proc.pid}")
        watcher.start()
        try:
            if on_start:
                on_start(proc.pid)
            for line in proc.stdout:
                if line.startswith(EVENT_PREFIX):
                    try:
                        event = json.loads(line[len(EVENT_PREFIX):])
                    except ValueError:
                        log.write(line)
                        continue
                    if event.get("event") == "done":
                        outcome["done"] = event
                    if on_event:
                        try:
                            on_event(event)
                        except Exception:
                            logger.warning("Trainer event handler failed for %s", event.get("event"),
                                           exc_info=True)
                    continue
                log.write(line)
                log.flush()
                if line.strip():
                    tail.append(line.rstrip())
            code = proc.wait()
        finally:
            finished.set()
            _terminate(proc, grace_s)
            watcher.join(timeout=grace_s + poll_s)

    log_tail = "\n".join(tail)
    if outcome.get("stopped"):
        raise TrainerStopped("The trainer was stopped on request.")
    if outcome.get("timed_out"):
        raise TrainerFailed(f"The trainer passed its time limit of {time_limit_s / 3600:.1f} hours "
                            f"and was stopped.", log_tail)
    if code != 0:
        last = tail[-1] if tail else "no output"
        raise TrainerFailed(f"The trainer exited with code {code}: {last}", log_tail)
    if "done" not in outcome:
        raise TrainerFailed("The trainer exited without reporting its result.", log_tail)
    return outcome["done"]
