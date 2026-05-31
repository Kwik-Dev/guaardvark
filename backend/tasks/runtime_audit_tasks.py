"""Celery tasks for the runtime-liveness layer (Phase 1).

Two beat-driven tasks:
  * runtime_audit.flush_hits     — drain the in-memory tracker to symbol_hits
                                    (short cadence; the worker_process_shutdown
                                    signal handles child recycle, this catches
                                    the steady state).
  * runtime_audit.prune_old_hits — delete rows older than the retention window
                                    BUT ONLY where static_reachability is not
                                    TRUE. Reachable-but-cold rows (once-a-month
                                    handlers) are kept on purpose.

Phase 1 is record + prune only. No consensus emission, no auto-dispatch — those
are Phase 2.
"""

import logging
import os
from datetime import datetime, timedelta

from celery import Celery

logger = logging.getLogger(__name__)


def create_runtime_audit_tasks(celery_app: Celery):
    @celery_app.task(name="runtime_audit.flush_hits", ignore_result=True)
    def flush_hits():
        """Drain the process-local execution tracker to the symbol_hits table."""
        try:
            from backend.services.execution_context_tracker import get_tracker
            flushed = get_tracker().flush()
            logger.debug("runtime_audit.flush_hits flushed %s symbols", flushed)
            return {"status": "ok", "flushed": flushed}
        except Exception as e:  # noqa: BLE001 - audit failure must never break beat
            logger.warning("runtime_audit.flush_hits failed (non-fatal): %s", e)
            return {"status": "error", "error": str(e)}

    @celery_app.task(name="runtime_audit.prune_old_hits", ignore_result=True)
    def prune_old_hits():
        """Delete stale symbol_hits older than the retention window.

        Never prunes rows where static_reachability IS TRUE — those are known
        reachable and being cold is expected (rare handlers). Rows that are
        NULL/false for reachability and stale are the audit signal we discard.
        """
        try:
            retention_days = int(os.environ.get("GUAARDVARK_RUNTIME_HITS_RETENTION_DAYS", "90"))
        except (TypeError, ValueError):
            retention_days = 90

        try:
            from backend.models import db, SymbolHit

            cutoff = datetime.now() - timedelta(days=retention_days)
            deleted = (
                db.session.query(SymbolHit)
                .filter(SymbolHit.last_fired_at < cutoff)
                .filter(SymbolHit.static_reachability.isnot(True))
                .delete(synchronize_session=False)
            )
            db.session.commit()
            logger.info(
                "runtime_audit.prune_old_hits deleted %s stale non-reachable rows (>%sd)",
                deleted, retention_days,
            )
            return {"status": "ok", "deleted": deleted, "retention_days": retention_days}
        except Exception as e:  # noqa: BLE001
            logger.warning("runtime_audit.prune_old_hits failed (non-fatal): %s", e)
            try:
                from backend.models import db
                db.session.rollback()
            except Exception:
                pass
            return {"status": "error", "error": str(e)}


def schedule_runtime_audit_tasks(celery_app: Celery):
    """Merge runtime-audit beat entries into the existing beat schedule."""
    celery_app.conf.beat_schedule = {
        **getattr(celery_app.conf, "beat_schedule", {}),
        "runtime-audit-flush-hits": {
            "task": "runtime_audit.flush_hits",
            "schedule": 300.0,  # every 5 minutes
            "options": {"queue": "default"},
        },
        "runtime-audit-prune-old-hits": {
            "task": "runtime_audit.prune_old_hits",
            "schedule": 86400.0,  # daily
            "options": {"queue": "default"},
        },
    }
