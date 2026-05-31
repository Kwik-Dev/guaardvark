"""Queue social outreach runs as first-class Task rows.

The Outreach HTTP API, chat tools, and slash commands should all use this
service instead of firing direct Celery tasks. That keeps user-initiated
Outreach visible in TaskPage, ActivityPage, and the unified progress footer.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from backend.models import Task, db
from backend.services.social_outreach import kill_switch, persona

logger = logging.getLogger(__name__)


OUTREACH_TASK_TYPES = {
    "reddit": "social_outreach_reddit",
    "self_share": "social_outreach_share",
    "share": "social_outreach_share",
    "recon": "social_outreach_recon",
    "draft": "social_outreach_draft",
    "discord": "social_outreach_discord",
}


def queue_outreach_run(
    platform: str,
    *,
    subreddit: str | None = None,
    link_url: str | None = None,
    batch_size: int | None = None,
    priority: int = 2,
    created_by: str = "outreach",
) -> dict[str, Any]:
    """Create and enqueue a Task-backed outreach run.

    Raises:
        ValueError: unsupported platform.
        RuntimeError: Outreach is disabled or Celery submission fails.
    """
    normalized = (platform or "").strip().lower()
    task_type = OUTREACH_TASK_TYPES.get(normalized)
    if not task_type:
        supported = ", ".join(sorted(OUTREACH_TASK_TYPES))
        raise ValueError(f"unsupported platform '{platform}'. Use one of: {supported}.")

    if not kill_switch.is_enabled():
        raise RuntimeError("outreach is disabled (kill switch is off)")

    workflow_config = {
        "platform": normalized,
        "created_by": created_by,
    }
    if subreddit:
        workflow_config["subreddit"] = subreddit.strip().lstrip("r/").lstrip("/")
    if link_url:
        workflow_config["link_url"] = link_url.strip()
    elif task_type == "social_outreach_share":
        workflow_config["link_url"] = persona.SITE_URL
    if batch_size is not None:
        workflow_config["batch_size"] = max(1, min(int(batch_size), 25))

    task = Task(
        name=_task_name(normalized, workflow_config),
        description=_task_description(normalized, workflow_config),
        status="queued",
        priority=priority,
        type=task_type,
        workflow_config=json.dumps(workflow_config),
        schedule_type="immediate",
        task_handler="social_outreach",
        handler_config={"platform": normalized, "source": created_by},
        progress=0,
    )
    db.session.add(task)
    db.session.commit()

    task.job_id = f"task_{task.id}"
    task.updated_at = datetime.datetime.now(datetime.timezone.utc)
    db.session.commit()

    try:
        from backend.tasks.unified_task_executor import execute_unified_task

        celery_result = execute_unified_task.apply_async(args=[task.id], queue="default")
    except Exception as exc:
        task.status = "failed"
        task.error_message = f"Failed to queue Outreach task: {exc}"
        task.updated_at = datetime.datetime.now(datetime.timezone.utc)
        db.session.commit()
        logger.exception("Failed to enqueue Outreach task %s", task.id)
        raise RuntimeError(str(exc)) from exc

    payload = task.to_dict()
    payload.update(
        {
            "task_id": task.id,
            "job_id": task.job_id,
            "celery_task_id": celery_result.id,
            "platform": normalized,
            "message": _queued_message(normalized, workflow_config, task.id),
        }
    )
    return payload


def _task_name(platform: str, workflow_config: dict[str, Any]) -> str:
    if platform == "reddit":
        subreddit = workflow_config.get("subreddit")
        return f"Outreach Reddit pass{f' for r/{subreddit}' if subreddit else ''}"
    if platform in ("self_share", "share"):
        return "Outreach self-share pass"
    if platform == "recon":
        return "Outreach recon pass"
    if platform == "draft":
        return "Outreach draft candidates"
    if platform == "discord":
        return "Outreach Discord pass"
    return "Outreach pass"


def _task_description(platform: str, workflow_config: dict[str, Any]) -> str:
    if platform == "reddit":
        subreddit = workflow_config.get("subreddit") or "next target from social_outreach_targets.json"
        return f"Discover, draft, and maybe post a Reddit Outreach comment for {subreddit}."
    if platform in ("self_share", "share"):
        return "Submit a Guaardvark self-share link post through the supervised Outreach pipeline."
    if platform == "recon":
        return "Scout the next configured Reddit target and add candidate Outreach rows."
    if platform == "draft":
        return "Draft pending Outreach candidates into reviewable queue rows."
    if platform == "discord":
        return "Run the Discord Outreach pass if the Discord cog is available."
    return "Run an Outreach pass."


def _queued_message(platform: str, workflow_config: dict[str, Any], task_id: int) -> str:
    if platform == "reddit" and workflow_config.get("subreddit"):
        return f"Reddit Outreach pass for r/{workflow_config['subreddit']} added to the Job Queue as task #{task_id}."
    labels = {
        "reddit": "Reddit Outreach pass",
        "self_share": "Self-share Outreach pass",
        "share": "Self-share Outreach pass",
        "recon": "Recon Outreach pass",
        "draft": "Drafting Outreach pass",
        "discord": "Discord Outreach pass",
    }
    return f"{labels.get(platform, 'Outreach pass')} added to the Job Queue as task #{task_id}."
