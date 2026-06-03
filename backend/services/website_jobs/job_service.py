"""Queue website runs (crawl, index, code) as first-class Task rows.

Mirrors backend/services/social_outreach/job_service.py so website work is visible
in the unified Jobs/Activity surfaces and is schedulable through the Task scheduler.
All website task types share a ``website_`` prefix; the jobs layer carves that prefix
into JobKind.WEBSITE (see job_registry + unified_jobs_resource_api), the same way
``social_outreach_`` maps to JobKind.OUTREACH.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Optional

from backend.models import Task, Website, db

logger = logging.getLogger(__name__)


def _parse_schedule(schedule_at: Any) -> Optional[datetime.datetime]:
    """Accept an ISO string or datetime; return a datetime or None."""
    if not schedule_at:
        return None
    if isinstance(schedule_at, datetime.datetime):
        return schedule_at
    try:
        return datetime.datetime.fromisoformat(str(schedule_at).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise ValueError(f"Invalid schedule_at datetime: {schedule_at!r}")


def _finalize(task: Task, schedule_at: Any) -> tuple[Any, dict]:
    """Either schedule the task for later (Task scheduler picks it up at due_date)
    or dispatch it immediately. Returns (celery_result_or_None, extra_payload)."""
    due = _parse_schedule(schedule_at)
    if due is not None:
        # Scheduled: the check_scheduled_tasks beat dispatches pending+due tasks.
        # Leave job_id NULL so the scheduler is allowed to claim it.
        task.status = "pending"
        task.schedule_type = "scheduled"
        task.due_date = due
        task.updated_at = datetime.datetime.now(datetime.timezone.utc)
        db.session.commit()
        return None, {
            "scheduled_for": due.isoformat(),
            "message": f"{task.name} scheduled for {due.isoformat()} (task #{task.id}).",
        }
    return _dispatch(task), {}


def _dispatch(task: Task) -> Any:
    """Stamp job_id and hand the task to the unified Celery executor."""
    task.job_id = f"task_{task.id}"
    task.updated_at = datetime.datetime.now(datetime.timezone.utc)
    db.session.commit()
    try:
        from backend.tasks.unified_task_executor import execute_unified_task

        return execute_unified_task.apply_async(args=[task.id], queue="default")
    except Exception as exc:
        task.status = "failed"
        task.error_message = f"Failed to queue website task: {exc}"
        task.updated_at = datetime.datetime.now(datetime.timezone.utc)
        db.session.commit()
        logger.exception("Failed to enqueue website task %s", task.id)
        raise RuntimeError(str(exc)) from exc


def queue_crawl_run(
    website_id: int,
    *,
    max_pages: Optional[int] = None,
    priority: int = 2,
    created_by: str = "websites",
    schedule_at: Any = None,
) -> dict[str, Any]:
    """Create + enqueue a Task-backed sitemap crawl for one website.

    If ``schedule_at`` (ISO datetime) is given, the run is scheduled for later
    (the Task scheduler dispatches it when due) instead of running now.
    Raises ValueError if the website does not exist.
    """
    site = db.session.get(Website, website_id)
    if site is None:
        raise ValueError(f"Website {website_id} not found")

    workflow_config: dict[str, Any] = {"website_id": website_id, "created_by": created_by}
    if max_pages is not None:
        workflow_config["max_pages"] = max(1, int(max_pages))

    task = Task(
        name=f"Crawl {site.url}",
        description=f"Walk the sitemap of {site.url} and persist each page.",
        status="queued",
        priority=priority,
        type="website_crawl",
        target_website=site.url,
        website_id=website_id,
        workflow_config=json.dumps(workflow_config),
        schedule_type="immediate",
        task_handler="website_crawl",
        handler_config={"website_id": website_id, "source": created_by},
        progress=0,
    )
    db.session.add(task)
    db.session.commit()

    celery_result, extra = _finalize(task, schedule_at)

    payload = task.to_dict()
    payload.update(
        {
            "task_id": task.id,
            "job_id": task.job_id,
            "celery_task_id": getattr(celery_result, "id", None),
            "message": f"Crawl of {site.url} added to the Job Queue as task #{task.id}.",
        }
    )
    payload.update(extra)
    return payload


def queue_code_run(
    website_id: int,
    *,
    mode: str = "swarm",
    instructions: str = "",
    priority: int = 2,
    created_by: str = "websites",
    schedule_at: Any = None,
) -> dict[str, Any]:
    """Create + enqueue a Task-backed local CODE run for a website's source folder.

    mode='swarm' → multi-agent edit via the swarm sidecar on Website.local_path.
    mode='agent' → single AgentExecutor run (PENDING: needs external-folder tool
    rooting before it can run safely — see executor branch).

    Raises ValueError if the website/local_path is missing or mode is invalid.
    """
    import os

    if mode not in ("swarm", "agent"):
        raise ValueError("mode must be 'swarm' or 'agent'")
    site = db.session.get(Website, website_id)
    if site is None:
        raise ValueError(f"Website {website_id} not found")
    local_path = (getattr(site, "local_path", None) or "").strip()
    if not local_path:
        raise ValueError("Website has no local_path set — add the local source folder in Settings first.")
    if not os.path.isdir(local_path):
        raise ValueError(f"local_path does not exist on disk: {local_path}")

    is_git = os.path.isdir(os.path.join(local_path, ".git"))
    workflow_config: dict[str, Any] = {
        "website_id": website_id,
        "local_path": local_path,
        "is_git": is_git,
        "mode": mode,
        "instructions": instructions,
        "created_by": created_by,
    }

    task = Task(
        name=f"Code {mode}: {site.url}",
        description=f"Run a {mode} code pass on {local_path}" + (f" — {instructions}" if instructions else ""),
        status="queued",
        priority=priority,
        type=f"website_code_{mode}",
        target_website=site.url,
        website_id=website_id,
        workflow_config=json.dumps(workflow_config),
        schedule_type="immediate",
        task_handler="website_code",
        handler_config={"website_id": website_id, "mode": mode, "source": created_by},
        progress=0,
    )
    db.session.add(task)
    db.session.commit()

    celery_result, extra = _finalize(task, schedule_at)

    payload = task.to_dict()
    payload.update(
        {
            "task_id": task.id,
            "job_id": task.job_id,
            "celery_task_id": getattr(celery_result, "id", None),
            "message": f"Code {mode} run for {site.url} added to the Job Queue as task #{task.id}.",
        }
    )
    payload.update(extra)
    return payload


def queue_index_run(
    website_id: int,
    *,
    max_n: Optional[int] = None,
    sync_first: bool = True,
    priority: int = 2,
    created_by: str = "websites",
    schedule_at: Any = None,
) -> dict[str, Any]:
    """Create + enqueue a Task-backed Google Indexing submission for one site.

    Replaces the old raw ``submit_indexing_batch_for_site.delay()`` dispatch so
    on-demand submits are visible in Activity and schedulable through the Task
    scheduler. If ``schedule_at`` is given the run is scheduled for later.
    Raises ValueError if the website does not exist.
    """
    site = db.session.get(Website, website_id)
    if site is None:
        raise ValueError(f"Website {website_id} not found")

    workflow_config: dict[str, Any] = {
        "website_id": website_id,
        "sync_first": bool(sync_first),
        "created_by": created_by,
    }
    if max_n is not None:
        workflow_config["max_n"] = max(1, int(max_n))

    task = Task(
        name=f"Index submit {site.url}",
        description=f"Sync sitemap and submit pending URLs of {site.url} to the Google Indexing API.",
        status="queued",
        priority=priority,
        type="website_index_submit",
        target_website=site.url,
        website_id=website_id,
        workflow_config=json.dumps(workflow_config),
        schedule_type="immediate",
        task_handler="website_index_submit",
        handler_config={"website_id": website_id, "source": created_by},
        progress=0,
    )
    db.session.add(task)
    db.session.commit()

    celery_result, extra = _finalize(task, schedule_at)

    payload = task.to_dict()
    payload.update(
        {
            "task_id": task.id,
            "job_id": task.job_id,
            "celery_task_id": getattr(celery_result, "id", None),
            "message": f"Indexing submission for {site.url} added to the Job Queue as task #{task.id}.",
        }
    )
    payload.update(extra)
    return payload
