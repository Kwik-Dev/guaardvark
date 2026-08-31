import logging

from celery import shared_task

logger = logging.getLogger(__name__)


# NOTE: use @shared_task (not @celery.task). Importing `celery` from
# backend.celery_app at module top creates a circular import when celery_app
# imports this module *during* create_celery_app() (the module-level `celery`
# singleton doesn't exist yet), which silently leaves the task unregistered and
# the worker rejects dispatched messages with "Received unregistered task".
# @shared_task binds to the default app (set via celery.set_default()) and is the
# pattern every other task module here uses.
@shared_task(bind=True)
def analyze_repository_task(self, folder_id):
    """
    Celery task to analyze a repository folder.
    """
    from backend.services.repository_analysis_service import RepositoryAnalysisService
    
    logger.info(f"Starting repository analysis for folder {folder_id}")
    try:
        RepositoryAnalysisService.analyze_repository(folder_id)
        logger.info(f"Completed repository analysis for folder {folder_id}")
        return {"status": "success", "folder_id": folder_id}
    except Exception as e:
        logger.error(f"Error in repository analysis task: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}
