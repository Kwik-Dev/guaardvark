"""Sending a task to the Celery worker, and saying so when it was not sent.

A task reaches the worker through Redis (CELERY_BROKER_URL). With the Redis
result backend the sender also subscribes to the task's result channel
(CELERY_RESULT_BACKEND, the same Redis by default) just before publishing.

A worker waits for Redis for as long as it takes
(broker_connection_max_retries=None in backend/celery_app.py): reconnecting is
its whole job. A process that sends a task (a web request, an MCP tool, the
beat scheduler) must not. With Celery's defaults a send with Redis stopped
spent 19 s retrying the result channel (20 attempts, 1 s apart) and then
raised "The Celery application must be restarted"; against an address that
never answers, each of those attempts waits for the kernel's connect timeout
(about two minutes on Linux).

The limits below bound the sending side. Measured against
redis://127.0.0.1:1/0 (nothing listening, so every connect is refused) the
send fails in about half a second; against a local listener that never
accepts, in about 6.5 s (three connect attempts of CONNECT_TIMEOUT_S plus one
pause). backend/tests/test_celery_dispatch.py checks both.

A worker keeps Celery's own result-store retries
(restore_worker_result_retries, run from the celeryd_init signal), so storing
a finished task's result still rides out a short Redis restart.

A send that fails because Redis did not answer raises TaskNotStarted, whichever
part of Celery noticed: kombu's OperationalError for the broker, Celery's
RuntimeError for the result channel. Its text names the task and the queue's
host and port, never credentials. A route that does not catch it answers
503 with that text (register_flask_handler, called from backend/app.py).
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlsplit

from celery import Celery
from kombu.exceptions import OperationalError

logger = logging.getLogger(__name__)

# Seconds one connection attempt to Redis may take. Redis on this machine or
# on the LAN accepts in milliseconds; only an address that drops the attempt
# (a host that is off, a firewall) gets near this.
CONNECT_TIMEOUT_S = 2.0

# Publishing to the broker: one reconnect after a failed publish, half a
# second later. A pooled connection opened before a Redis restart fails once
# and then works, so one retry is enough; more only delays the answer.
PUBLISH_RETRY_POLICY = {
    "max_retries": 1,
    "interval_start": 0.5,
    "interval_step": 0.5,
    "interval_max": 1.0,
}

# Subscribing to the result channel before publishing, which the Redis result
# backend does first: the same one reconnect. Celery's own policy (20 tries,
# 1 s apart) is what a worker keeps for storing results.
RESULT_RETRY_POLICY = dict(PUBLISH_RETRY_POLICY)

# Not bounded: a Redis that accepts connections but never replies still holds
# a send, since the broker connection has no read timeout (kombu's default) and
# adding one would change the connection the worker consumes on as well.

QUEUE_UNREACHABLE_CODE = "task_queue_unreachable"
_REASON_LIMIT = 200


def apply_sender_bounds(conf) -> None:
    """Bound how long sending a task waits for Redis (conf: a Celery app's)."""
    conf.broker_connection_timeout = CONNECT_TIMEOUT_S
    conf.task_publish_retry_policy = dict(PUBLISH_RETRY_POLICY)
    conf.broker_transport_options = {
        **(conf.broker_transport_options or {}),
        "socket_connect_timeout": CONNECT_TIMEOUT_S,
    }
    conf.redis_socket_connect_timeout = CONNECT_TIMEOUT_S
    conf.result_backend_transport_options = {
        **(conf.result_backend_transport_options or {}),
        "retry_policy": dict(RESULT_RETRY_POLICY),
    }


def restore_worker_result_retries(conf) -> None:
    """In a worker, storing a task's result keeps Celery's own retry policy.

    A worker never subscribes before sending (Celery skips it there), so the
    result-channel bound only ever shortened its result writes.
    """
    options = dict(conf.result_backend_transport_options or {})
    options.pop("retry_policy", None)
    conf.result_backend_transport_options = options


def _connection_errors() -> tuple:
    errors = [OperationalError, ConnectionError, TimeoutError]
    try:
        import redis.exceptions as redis_errors

        errors += [redis_errors.ConnectionError, redis_errors.TimeoutError]
    except ImportError:
        pass
    return tuple(errors)


_CONNECTION_ERRORS = _connection_errors()


def unreachable_cause(exc: BaseException) -> Optional[BaseException]:
    """The connection error behind ``exc`` (itself included), innermost first;
    None when ``exc`` is not a failure to reach the queue."""
    found = None
    seen = 0
    current: Optional[BaseException] = exc
    while current is not None and seen < 10:
        if isinstance(current, _CONNECTION_ERRORS):
            found = current
        current = current.__cause__
        seen += 1
    return found


def queue_address(app) -> str:
    """host:port of the broker an app sends to (no user name or password)."""
    url = ""
    try:
        url = app.conf.broker_write_url or app.conf.broker_url or ""
    except Exception:  # noqa: BLE001 - a description, never a reason to fail
        pass
    try:
        parts = urlsplit(url if "://" in url else f"redis://{url}")
        host = parts.hostname or "localhost"
        port = parts.port
    except ValueError:
        return "the configured broker"
    host = f"[{host}]" if ":" in host else host
    return f"{host}:{port}" if port else host


class TaskNotStarted(OperationalError):
    """The background queue did not take a task, so it will not run.

    An OperationalError, as kombu raises for an unreachable broker, so code
    written against kombu still catches it.
    """

    code = QUEUE_UNREACHABLE_CODE

    def __init__(self, task_name: str, queue: str, reason: str):
        self.task_name = task_name
        self.queue = queue
        self.reason = reason
        super().__init__(
            f"Task {task_name} was not started: {self.why}. Start Redis "
            "(./start.sh starts it) and try again."
        )

    @property
    def why(self) -> str:
        """The cause alone, for a caller that words "X was not started" itself."""
        return f"Guaardvark's background queue (Redis at {self.queue}) is not reachable ({self.reason})"


def not_started(task_name: str, app, exc: BaseException) -> Optional[TaskNotStarted]:
    """TaskNotStarted for ``exc`` when it means the queue was not reachable."""
    if isinstance(exc, TaskNotStarted):
        return exc
    cause = unreachable_cause(exc)
    if cause is None:
        return None
    reason = (str(cause).strip() or type(cause).__name__)[:_REASON_LIMIT]
    return TaskNotStarted(task_name, queue_address(app), reason)


class GuaardvarkCelery(Celery):
    """Celery whose send_task, and so every apply_async, delay, group and
    chain, raises TaskNotStarted when Redis did not take the task."""

    def send_task(self, name, *args, **kwargs):
        try:
            return super().send_task(name, *args, **kwargs)
        except Exception as exc:
            failure = not_started(name, self, exc)
            if failure is None or failure is exc:
                raise
            logger.warning("%s", failure)
            raise failure from exc


HEALTH_PING_TASK = "backend.celery_tasks_isolated.ping"


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def ping_worker(app, timeout: float = 5.0) -> tuple[bool, str]:
    """Send the health ping and wait for a worker's answer.

    Returns (True, "up: <answer>") or (False, "down: <reason>"), one line
    either way, for `flask celery-health`.
    """
    from celery.exceptions import TimeoutError as ResultTimeout

    try:
        result = app.send_task(HEALTH_PING_TASK, queue="health")
    except TaskNotStarted as e:
        return False, f"down: {e.why}. Start Redis (./start.sh starts it)."
    try:
        answer = result.get(timeout=timeout)
    except ResultTimeout:
        return False, (
            f"down: Redis took the ping but no worker answered within {timeout:g} s; "
            "the Celery worker is not running (./start.sh starts it) or is busy with a long task."
        )
    except Exception as e:  # noqa: BLE001 - reported as the reason
        failure = not_started(HEALTH_PING_TASK, app, e)
        if failure is not None:
            return False, f"down: {failure.why}. Start Redis (./start.sh starts it)."
        return False, _one_line(f"down: {type(e).__name__}: {e}")
    return True, _one_line(f"up: {answer}")


def register_flask_handler(flask_app) -> None:
    """Answer TaskNotStarted from any route with 503 and the reason.

    Redis being down is not a fault in the route, so it does not reach the
    500 handler (which feeds self-improvement). The route's uncommitted
    changes are rolled back, as for any other error.
    """
    from flask import jsonify, request

    @flask_app.errorhandler(TaskNotStarted)
    def _task_not_started(e):
        flask_app.logger.warning(f"Task not started ({request.method} {request.path}): {e}")
        try:
            from backend.models import db

            db.session.rollback()
        except Exception as rb_err:  # noqa: BLE001 - the answer below still goes out
            flask_app.logger.error(f"Rollback failed: {rb_err}")
        return jsonify({"error": str(e), "code": e.code, "task": e.task_name}), 503


def mark_progress_not_started(progress_id: Optional[str], exc: BaseException) -> None:
    """Close a progress entry made for a task that never reached the worker,
    so the UI shows the reason instead of a job parked at 0 %."""
    if not progress_id:
        return
    try:
        from backend.utils.unified_progress_system import get_unified_progress

        get_unified_progress().error_process(progress_id, str(exc))
    except Exception:  # noqa: BLE001 - the caller is already reporting exc
        logger.debug("Could not close progress %s", progress_id, exc_info=True)
