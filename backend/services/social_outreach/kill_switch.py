"""
Kill switch + cadence enforcement for outreach.

Three layers of brakes:
  1. is_enabled() — global on/off via Setting('social_outreach_enabled', 'true'/'false'). Defaults false.
  2. is_supervised() — when true, drafts queue for review instead of posting. Defaults false (per user choice; flip to true if first night looks bot-y).
  3. cadence checks — Redis-backed, per-platform. Hard caps: 1 post / 30 min / platform, 8 posts / 24h / platform.

Plus task-level abort on 2 servo failures (enforced by the loop, not here).

If Redis is unavailable we fail closed — better to not post than to spam.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Hard caps — change these in code, not config, so a misclick can't open the firehose.
CADENCE_MIN_GAP_SECONDS = 30 * 60        # 1 per 30 min per platform
CADENCE_DAILY_CAP = 8                    # 8 per day per platform
CADENCE_DAILY_WINDOW_SECONDS = 24 * 3600
SERVO_FAILURE_ABORT_THRESHOLD = 2

REDIS_KEY_LAST_POST = "social_outreach:last_post:{platform}"
REDIS_KEY_DAILY_LIST = "social_outreach:posts_24h:{platform}"  # zset of timestamps


def _get_redis():
    """Lazy redis client. Returns None if unreachable."""
    try:
        import redis
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)
        client.ping()
        return client
    except Exception as e:
        logger.warning("redis unavailable for cadence: %s", e)
        return None


_direct_engine = None


def _get_direct_engine():
    """SQLAlchemy engine for reads outside Flask app context (Celery beat early-exit)."""
    global _direct_engine
    if _direct_engine is None:
        from sqlalchemy import create_engine

        url = os.environ.get(
            "DATABASE_URL",
            "postgresql://guaardvark:guaardvark@localhost:5432/guaardvark",
        )
        _direct_engine = create_engine(url, pool_pre_ping=True)
    return _direct_engine


def _read_setting_direct(key: str, default: str) -> str:
    try:
        from sqlalchemy import text

        with _get_direct_engine().connect() as conn:
            row = conn.execute(
                text("SELECT value FROM settings WHERE key = :key"),
                {"key": key},
            ).fetchone()
            if row and row[0] is not None:
                return str(row[0])
    except Exception as e:
        logger.warning("direct setting read failed for %s: %s", key, e)
    return default


def _read_setting(key: str, default: str) -> str:
    try:
        from flask import has_app_context

        if has_app_context():
            from backend.models import Setting

            row = Setting.query.filter_by(key=key).first()
            if row and row.value is not None:
                return str(row.value)
            return default
    except Exception as e:
        logger.warning("setting read failed for %s: %s", key, e)
    return _read_setting_direct(key, default)


def is_enabled() -> bool:
    val = _read_setting("social_outreach_enabled", "false").strip().lower()
    return val in ("true", "1", "yes", "on")


def is_supervised() -> bool:
    val = _read_setting("social_outreach_supervised", "false").strip().lower()
    return val in ("true", "1", "yes", "on")


def set_enabled(value: bool) -> None:
    _write_setting("social_outreach_enabled", "true" if value else "false")


def set_supervised(value: bool) -> None:
    _write_setting("social_outreach_supervised", "true" if value else "false")


def _write_setting(key: str, value: str) -> None:
    try:
        from backend.models import Setting, db
        row = Setting.query.filter_by(key=key).first()
        if row is None:
            row = Setting(key=key, value=value)
            db.session.add(row)
        else:
            row.value = value
        db.session.commit()
    except Exception as e:
        logger.error("setting write failed for %s: %s", key, e)
        try:
            from backend.models import db
            db.session.rollback()
        except Exception:
            pass


def cadence_allows_post(platform: str) -> tuple[bool, Optional[str]]:
    """
    Returns (allowed, reason_if_not). Fails closed on Redis errors.
    """
    r = _get_redis()
    if r is None:
        return False, "redis unavailable (failing closed)"

    now = time.time()

    last_post_key = REDIS_KEY_LAST_POST.format(platform=platform)
    last = r.get(last_post_key)
    if last is not None:
        try:
            elapsed = now - float(last)
            if elapsed < CADENCE_MIN_GAP_SECONDS:
                return False, f"too soon ({int(elapsed)}s since last, need {CADENCE_MIN_GAP_SECONDS}s)"
        except ValueError:
            pass

    daily_key = REDIS_KEY_DAILY_LIST.format(platform=platform)
    cutoff = now - CADENCE_DAILY_WINDOW_SECONDS
    r.zremrangebyscore(daily_key, 0, cutoff)
    count_24h = r.zcard(daily_key)
    if count_24h >= CADENCE_DAILY_CAP:
        return False, f"daily cap hit ({count_24h}/{CADENCE_DAILY_CAP} in 24h)"

    return True, None


def record_post(platform: str) -> None:
    r = _get_redis()
    if r is None:
        logger.warning("record_post: redis unavailable, cadence will under-count")
        return
    now = time.time()
    r.set(REDIS_KEY_LAST_POST.format(platform=platform), str(now))
    r.zadd(REDIS_KEY_DAILY_LIST.format(platform=platform), {str(now): now})
    r.expire(REDIS_KEY_DAILY_LIST.format(platform=platform), CADENCE_DAILY_WINDOW_SECONDS + 60)


def _celery_message_is_outreach(raw) -> bool:
    """True if a Redis-broker Celery message is a social_outreach.* task."""
    try:
        import json

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        msg = json.loads(raw)
        task = (msg.get("headers") or {}).get("task") or ""
        return task.startswith("social_outreach.")
    except Exception:
        return False


def drain_pending_outreach_tasks(queues: tuple[str, ...] = ("default", "celery")) -> dict:
    """Revoke in-flight and drop queued social_outreach.* broker messages."""
    purged = 0
    revoked = 0
    errors: list[str] = []

    try:
        from backend.celery_app import celery

        insp = celery.control.inspect(timeout=3.0)
        prefix = "social_outreach."
        for snapshot in (
            insp.active() or {},
            insp.reserved() or {},
        ):
            for _worker, tasks in snapshot.items():
                for task in tasks:
                    name = task.get("name") or ""
                    if name.startswith(prefix):
                        celery.control.revoke(
                            task["id"],
                            terminate=True,
                            signal="SIGTERM",
                        )
                        revoked += 1
        for _worker, entries in (insp.scheduled() or {}).items():
            for entry in entries:
                req = entry.get("request") or {}
                name = req.get("name") or ""
                if name.startswith(prefix):
                    celery.control.revoke(req["id"], terminate=False)
                    revoked += 1
    except Exception as e:
        errors.append(f"revoke: {e}")

    r = _get_redis()
    if r is None:
        errors.append("purge: redis unavailable")
    else:
        try:
            for queue_name in queues:
                length = r.llen(queue_name)
                if not length:
                    continue
                kept: list = []
                for raw in r.lrange(queue_name, 0, -1):
                    if _celery_message_is_outreach(raw):
                        purged += 1
                    else:
                        kept.append(raw)
                pipe = r.pipeline()
                pipe.delete(queue_name)
                if kept:
                    pipe.rpush(queue_name, *kept)
                pipe.execute()
        except Exception as e:
            errors.append(f"purge: {e}")

    return {"purged": purged, "revoked": revoked, "errors": errors}


def cancel_outreach_task_rows() -> dict:
    """Mark queued/in-progress Task-backed outreach runs as cancelled."""
    cancelled = 0
    revoked = 0
    errors: list[str] = []

    try:
        from flask import has_app_context

        if not has_app_context():
            return {"cancelled_tasks": 0, "revoked_tasks": 0, "errors": ["no app context"]}

        from backend.models import Task, db

        rows = (
            Task.query.filter(Task.task_handler == "social_outreach")
            .filter(Task.status.in_(("queued", "pending", "in-progress")))
            .all()
        )
        if not rows:
            return {"cancelled_tasks": 0, "revoked_tasks": 0, "errors": errors}

        try:
            from backend.celery_app import celery
        except Exception as e:
            celery = None
            errors.append(f"celery import: {e}")

        for row in rows:
            if celery and row.celery_task_id:
                try:
                    celery.control.revoke(
                        row.celery_task_id,
                        terminate=True,
                        signal="SIGTERM",
                    )
                    revoked += 1
                except Exception as e:
                    errors.append(f"revoke task {row.id}: {e}")
            row.status = "cancelled"
            cancelled += 1
        db.session.commit()
    except Exception as e:
        errors.append(f"cancel tasks: {e}")
        try:
            from backend.models import db

            db.session.rollback()
        except Exception:
            pass

    return {"cancelled_tasks": cancelled, "revoked_tasks": revoked, "errors": errors}


def apply_kill_switch() -> dict:
    """Hard-stop outreach: flip off, drain broker queue, cancel Task rows."""
    set_enabled(False)
    result = {"enabled": False}
    result.update(drain_pending_outreach_tasks())
    result.update(cancel_outreach_task_rows())
    return result


def cadence_status() -> dict:
    """Snapshot for /status endpoint."""
    r = _get_redis()
    out = {}
    platforms = ["reddit", "discord", "facebook"]
    if r is None:
        for p in platforms:
            out[p] = {"redis": "unavailable"}
        return out
    now = time.time()
    for p in platforms:
        last = r.get(REDIS_KEY_LAST_POST.format(platform=p))
        daily_key = REDIS_KEY_DAILY_LIST.format(platform=p)
        r.zremrangebyscore(daily_key, 0, now - CADENCE_DAILY_WINDOW_SECONDS)
        out[p] = {
            "last_post_seconds_ago": (int(now - float(last)) if last else None),
            "posts_in_24h": r.zcard(daily_key),
            "min_gap_s": CADENCE_MIN_GAP_SECONDS,
            "daily_cap": CADENCE_DAILY_CAP,
        }
    return out
