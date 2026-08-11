"""Celery tasks for RAG Autoresearch — idle detection, scheduled runs, event triggers."""
import logging

logger = logging.getLogger(__name__)


def create_autoresearch_tasks(celery_app):
    """Create autoresearch Celery tasks."""

    @celery_app.task(name="autoresearch.check_idle")
    def check_idle_and_start():
        """Runs every 60s. Starts autoresearch if system is idle."""
        try:
            from backend.models import Setting
            from backend.services.rag_autoresearch_service import get_autoresearch_service

            svc = get_autoresearch_service()
            if svc.is_running():
                return

            idle_setting = Setting.query.filter_by(key="rag_autoresearch_idle_minutes").first()
            idle_minutes = int(idle_setting.value) if idle_setting else 10

            # Default OFF: auto-start must be an explicit opt-in. It used to
            # default on, and in this process is_idle() is ALWAYS true after 10
            # minutes of worker uptime — activity tracking lives in the web
            # process and never updates the Celery-side singleton. Combined with
            # an unbounded run_loop and an empty eval set, that self-started the
            # 2026-08 134M-row runaway on a box nobody had opted in on.
            auto_setting = Setting.query.filter_by(key="rag_autoresearch_auto_enabled").first()
            auto_enabled = (auto_setting.value.lower() == "true") if auto_setting else False

            if not auto_enabled:
                return

            if svc.is_idle(idle_minutes=idle_minutes):
                max_setting = Setting.query.filter_by(key="rag_autoresearch_max_experiments").first()
                # 0 means "no user override", never "unbounded" — run_loop caps
                # it at AUTORESEARCH_MAX_EXPERIMENTS_PER_RUN.
                max_exp = int(max_setting.value) if max_setting and max_setting.value != "0" else 0

                logger.info(f"System idle for >{idle_minutes}m — starting autoresearch")
                svc.run_loop(max_experiments=max_exp)
        except Exception as e:
            logger.error(f"Autoresearch idle check failed: {e}")

    @celery_app.task(name="autoresearch.on_index_complete")
    def on_index_complete():
        """Called after indexing completes — marks eval set as potentially stale."""
        try:
            from backend.services.rag_autoresearch_service import get_autoresearch_service
            svc = get_autoresearch_service()
            if svc.eval_harness.is_stale():
                logger.info("Eval set is stale after indexing — will regenerate on next run")
        except Exception as e:
            logger.error(f"Post-index eval check failed: {e}")


def schedule_autoresearch_tasks(celery_app):
    """Register autoresearch Beat schedule."""
    celery_app.conf.beat_schedule.update({
        "autoresearch-idle-check": {
            "task": "autoresearch.check_idle",
            "schedule": 60.0,
        },
    })
