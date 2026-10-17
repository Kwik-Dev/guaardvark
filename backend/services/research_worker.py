"""The research worker: the Celery worker that runs autoresearch, started on demand.

A research run owns the process that executes it for up to the run's wall-clock
budget plus the task limit above it (research_run_service.EXECUTE_RUN_TIME_LIMIT_S,
13 hours). On the main worker that put every chat helper, indexing job and render
behind it, so ``autoresearch.execute_run`` is routed to the ``research`` queue and
consumed here: one solo process, started by ``dispatch`` when a run is queued and
stopped by itself once it has had nothing to do for IDLE_EXIT_S. A box that never
runs autoresearch never starts it.

How it shares the machine:

- It cannot see the GPU (``CUDA_VISIBLE_DEVICES`` is empty from launch, as it is
  in every Celery worker once indexing_service is imported), so it holds no VRAM
  of its own. Its model calls go to Ollama, and each one first waits
  while a render or training job holds the cross-process GPU lease
  (backend/utils/gpu_check.wait_for_gpu_turn, wired in by research_run_service).
- It runs at a lower CPU priority (NICE_INCREMENT) and is the first of our
  processes the kernel's OOM killer picks (OOM_SCORE_ADJ).

stop.sh and start.sh stop it with the checkout's other Celery processes: both kill
every ``celery ... worker`` whose working directory is the checkout.
"""
from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

logger = logging.getLogger(__name__)

QUEUE = "research"
NODE = "research"  # celery node name: research@<host>
LOG_NAME = "celery_research.log"
PID_NAME = "celery_research.pid"
LOCK_NAME = "celery_research.lock"
# Set in the worker's environment by worker_env(); install() acts only when it is.
ENV_MARKER = "GUAARDVARK_RESEARCH_WORKER"

# The first message is queued before the worker has finished booting, so the idle
# timer only has to cover the moment between "ready" and receiving it. Two minutes
# also lets a run started right after another reuse the worker. Measured
# 2026-10-16: spawn to "ready" in about 1 s with warm file caches, 240 MB RSS idle.
IDLE_EXIT_S = 120.0
IDLE_POLL_S = 15.0
NICE_INCREMENT = 10
# Every Guaardvark process raises itself to +500 (backend/oom_priority.py); a
# background research run should go before any of them.
OOM_SCORE_ADJ = 800


class WorkerStartError(RuntimeError):
    """The research worker could not be started."""


def checkout_root() -> Path:
    """The checkout this code runs from (the worker's cwd, so stop.sh finds it)."""
    return Path(__file__).resolve().parents[2]


def worker_argv(root: Path) -> list[str]:
    return [
        sys.executable, "-m", "celery", "-A", "backend.celery_app.celery", "worker",
        f"--hostname={NODE}@%h",
        f"--queues={QUEUE}",
        "--pool=solo",
        "--concurrency=1",
        "--loglevel=info",
        f"--logfile={root / 'logs' / LOG_NAME}",
    ]


def worker_env(root: Path, base: Optional[dict] = None) -> dict:
    """The environment of the process that starts it, made into a worker's.

    It is started from the backend or from the main worker, so it inherits the
    .env those were started with; what start_celery.sh adds for a worker is added
    here, and what only the web process carries is dropped.
    """
    env = dict(os.environ if base is None else base)
    for key in [k for k in env if k.startswith("WERKZEUG_")]:
        env.pop(key)
    env.pop("GUAARDVARK_MCP_PROCESS", None)
    pythonpath = [str(root)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    env.update({
        ENV_MARKER: "1",
        "CELERY_WORKER_MODE": "true",
        "GUAARDVARK_ENHANCED_MODE": "true",
        "GUAARDVARK_ROOT": str(root),
        "CUDA_VISIBLE_DEVICES": "",
        "PYTHONPATH": os.pathsep.join(pythonpath),
    })
    # 0 is the operator switching the adjustment off; respect it.
    raw = env.get("GUAARDVARK_OOM_SCORE_ADJ", "500").strip()
    try:
        current = int(raw)
    except ValueError:
        current = 500
    if current != 0:
        env["GUAARDVARK_OOM_SCORE_ADJ"] = str(max(current, OOM_SCORE_ADJ))
    return env


def _pid_path(root: Path) -> Path:
    return root / "pids" / PID_NAME


def _is_research_worker(pid: int) -> bool:
    """A live, non-zombie process whose command line is a research worker's."""
    try:
        import psutil

        proc = psutil.Process(pid)
        if proc.status() == psutil.STATUS_ZOMBIE:
            return False
        cmd = " ".join(proc.cmdline())
    except Exception:  # noqa: BLE001 - gone, or not ours to read
        return False
    return "celery" in cmd and f"--queues={QUEUE}" in cmd


def running_pid(root: Optional[Path] = None) -> Optional[int]:
    """PID of this checkout's research worker while it runs, else None."""
    root = root or checkout_root()
    try:
        pid = int(_pid_path(root).read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _is_research_worker(pid) else None


@contextlib.contextmanager
def _launch_lock(root: Path) -> Iterator[None]:
    """Serializes "start if needed, then queue" against the worker's idle exit.

    Without it a worker could find its queue empty and stop in the same moment a
    run is queued for it, leaving the run waiting for a worker nobody starts.
    Platforms without fcntl have no lock; the window is then a few milliseconds.
    """
    path = root / "pids" / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:
        yield
        return
    with open(path, "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _reap(proc: subprocess.Popen, log_path: Path) -> None:
    code = proc.wait()
    if code:
        logger.warning("research worker (PID %s) exited with %s; see %s", proc.pid, code, log_path)
    else:
        logger.info("research worker (PID %s) stopped", proc.pid)


def _spawn(root: Path) -> int:
    log_path = root / "logs" / LOG_NAME
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _pid_path(root).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "ab") as log:
            proc = subprocess.Popen(
                worker_argv(root),
                cwd=str(root),
                env=worker_env(root),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        _pid_path(root).write_text(f"{proc.pid}\n")
    except OSError as e:
        raise WorkerStartError(f"could not start the research worker: {e}") from e
    # Waited on so a finished worker does not linger as a zombie under its parent.
    threading.Thread(
        target=_reap, args=(proc, log_path), name="research-worker-reaper", daemon=True,
    ).start()
    logger.info("research worker started (PID %s, queue %s, log %s)", proc.pid, QUEUE, log_path)
    return proc.pid


def ensure_running(root: Optional[Path] = None) -> int:
    """Start the research worker unless it already runs; returns its PID."""
    root = root or checkout_root()
    return running_pid(root) or _spawn(root)


def dispatch(send: Callable[[], Any], *, root: Optional[Path] = None) -> Any:
    """Make sure the research worker runs, then call ``send`` to queue its work.

    Raises WorkerStartError, before ``send`` is called, when it cannot be started.
    """
    root = root or checkout_root()
    with _launch_lock(root):
        ensure_running(root)
        return send()


# ---- inside the research worker ------------------------------------------------

def _queue_length(celery_app) -> int:
    """Messages waiting in the research queue.

    Redis drops an empty list, so a passive declare of an empty queue reports it
    missing; that counts as nothing waiting.
    """
    from amqp.exceptions import ChannelError as AmqpChannelError
    from kombu.exceptions import ChannelError

    with celery_app.connection_for_read() as conn:
        try:
            return int(conn.default_channel.queue_declare(queue=QUEUE, passive=True).message_count)
        except (ChannelError, AmqpChannelError):
            return 0


def _has_requests() -> bool:
    """A task is running here, or has been received and not started yet."""
    from celery.worker import state

    return bool(len(state.active_requests) or len(state.reserved_requests))


def _clear_pid_file(root: Optional[Path] = None, pid: Optional[int] = None) -> None:
    """Remove the pid file if it names this process (or ``pid``)."""
    path = _pid_path(root or checkout_root())
    try:
        if int(path.read_text().strip()) == (pid or os.getpid()):
            path.unlink()
    except (OSError, ValueError):
        pass


class IdleWatch:
    """Stops the research worker once it has been idle for ``idle_exit_s``.

    Idle means no request received or running and nothing waiting in the queue.
    The queue is read under the launch lock, and the pid file is removed before
    the stop, so a run queued from then on starts a new worker.
    """

    def __init__(self, celery_app, root: Path, *, idle_exit_s: float = IDLE_EXIT_S,
                 clock: Callable[[], float] = time.monotonic,
                 has_requests: Callable[[], bool] = _has_requests,
                 queue_length: Optional[Callable[[], int]] = None,
                 stop: Optional[Callable[[], None]] = None):
        self._root = root
        self._idle_exit_s = idle_exit_s
        self._clock = clock
        self._has_requests = has_requests
        self._queue_length = queue_length or (lambda: _queue_length(celery_app))
        self._stop = stop or (lambda: os.kill(os.getpid(), signal.SIGTERM))
        self._idle_since: Optional[float] = None

    def check(self) -> bool:
        """One look. True once the worker has been told to stop."""
        now = self._clock()
        if self._has_requests():
            self._idle_since = None
            return False
        if self._idle_since is None:
            self._idle_since = now
            return False
        if now - self._idle_since < self._idle_exit_s:
            return False
        with _launch_lock(self._root):
            try:
                waiting = self._queue_length()
            except Exception as e:  # noqa: BLE001 - broker down: stay, look again
                logger.warning("research worker: could not read the %s queue (%s)", QUEUE, e)
                return False
            if waiting or self._has_requests():
                self._idle_since = None
                return False
            logger.info("research worker: idle for %.0f s with nothing queued; stopping",
                        now - self._idle_since)
            _clear_pid_file(self._root)
            self._stop()
        return True


def _watch(watch: IdleWatch, poll_s: float) -> None:
    while True:
        time.sleep(poll_s)
        try:
            if watch.check():
                return
        except Exception:  # noqa: BLE001 - keep watching; a stuck worker is worse
            logger.exception("research worker idle check failed")


def install(celery_app) -> None:
    """Connect the research worker's start-up and idle exit. No-op elsewhere."""
    if os.environ.get(ENV_MARKER) != "1":
        return
    from celery.signals import worker_init, worker_ready, worker_shutdown

    @worker_init.connect(weak=False)
    def _lower_priority(**_ignored):
        if hasattr(os, "nice"):
            try:
                os.nice(NICE_INCREMENT)
            except OSError as e:
                logger.warning("research worker: could not lower its CPU priority: %s", e)

    @worker_ready.connect(weak=False)
    def _start_idle_watch(**_ignored):
        watch = IdleWatch(celery_app, checkout_root(), idle_exit_s=IDLE_EXIT_S)
        threading.Thread(
            target=_watch, args=(watch, IDLE_POLL_S), name="research-worker-idle", daemon=True,
        ).start()

    @worker_shutdown.connect(weak=False)
    def _forget_pid(**_ignored):
        _clear_pid_file()
