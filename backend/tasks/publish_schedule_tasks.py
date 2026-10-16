"""Celery entry point for scheduled publishing (see connections/scheduler.py).

DB access uses the app context the worker's ContextTask pushes around every
task (backend/celery_app.py).
"""

from __future__ import annotations

from celery import shared_task


@shared_task(name="connections.publish_due_tick")
def publish_due_tick():
    """Beat-driven, every minute while anything is scheduled."""
    from backend.services.connections import scheduler

    return scheduler.tick()
