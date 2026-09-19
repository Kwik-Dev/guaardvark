#!/usr/bin/env python3
"""Remove stuck and stale job directories from the unified progress store.

The store is ``<OUTPUT_DIR>/.progress_jobs/<job_id>/metadata.json``, written by
``backend/utils/unified_progress_system.py``. A directory outlives its job when
the process that owned it died before calling complete/error, or when the
backend restarted before the 60s cleanup timer fired; the Jobs page then keeps
showing a job nothing is working on.

Dry run by default. ``--execute`` removes. Callers in
``backend/tasks/cleanup_tasks.py`` and ``backend/api/system_api.py`` parse the
single ``Cleaned up N orphaned jobs`` summary line, so that line is printed on
every run - N is 0 on a dry run, because nothing was removed.
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TERMINAL_STATUSES = {"complete", "error", "cancelled"}

DEFAULT_MAX_AGE_HOURS = 24
DEFAULT_MAX_AGE_DAYS = 7


def resolve_progress_dir(output_dir: Optional[str] = None) -> Path:
    """Locate ``.progress_jobs`` for an explicit output dir, or the configured one."""
    if output_dir:
        return Path(output_dir) / ".progress_jobs"

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.config import OUTPUT_DIR

    return Path(OUTPUT_DIR) / ".progress_jobs"


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _last_activity(job_dir: Path, metadata: Dict[str, Any]) -> datetime:
    """Most recent moment the job showed a sign of life.

    Falls back to the directory mtime so a job whose metadata carries no usable
    timestamp is still aged rather than treated as brand new forever.
    """
    for key in ("last_update_utc", "completion_time_utc", "start_time_utc", "created_at"):
        stamp = _parse_timestamp(metadata.get(key))
        if stamp:
            return stamp
    return datetime.fromtimestamp(job_dir.stat().st_mtime, tz=timezone.utc)


def _read_metadata(job_dir: Path) -> Optional[Dict[str, Any]]:
    metadata_file = job_dir / "metadata.json"
    if not metadata_file.exists():
        return None
    try:
        raw = metadata_file.read_text(encoding="utf-8")
        parsed = json.loads(raw) if raw.strip() else None
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def classify_jobs(
    progress_dir: Path,
    max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    clean_completed: bool = False,
    now: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split the store into (removable, kept) job descriptions.

    Removable covers unreadable directories and non-terminal jobs idle past
    ``max_age_hours``; terminal jobs are only removable under
    ``clean_completed``, and then only past ``max_age_days``.
    """
    now = now or datetime.now(timezone.utc)
    stuck_cutoff = now - timedelta(hours=max_age_hours)
    completed_cutoff = now - timedelta(days=max_age_days)

    removable: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []

    if not progress_dir.exists():
        return removable, kept

    for job_dir in sorted(progress_dir.iterdir()):
        if not job_dir.is_dir():
            continue

        metadata = _read_metadata(job_dir)
        if metadata is None:
            removable.append({
                "job_id": job_dir.name,
                "path": str(job_dir),
                "status": "unknown",
                "process_type": "unknown",
                "reason": "no readable metadata.json",
            })
            continue

        status = str(metadata.get("status", "unknown")).lower()
        is_terminal = bool(metadata.get("is_complete")) or status in TERMINAL_STATUSES
        last_activity = _last_activity(job_dir, metadata)
        idle_hours = (now - last_activity).total_seconds() / 3600
        entry = {
            "job_id": metadata.get("job_id", job_dir.name),
            "path": str(job_dir),
            "status": status,
            "process_type": metadata.get("process_type", "unknown"),
            "idle_hours": round(idle_hours, 1),
        }

        if is_terminal:
            if clean_completed and last_activity < completed_cutoff:
                entry["reason"] = f"finished ({status}) {idle_hours / 24:.1f}d ago"
                removable.append(entry)
            else:
                kept.append(entry)
            continue

        if last_activity < stuck_cutoff:
            entry["reason"] = f"status {status}, no update for {idle_hours:.1f}h"
            removable.append(entry)
        else:
            kept.append(entry)

    return removable, kept


def remove_jobs(jobs: List[Dict[str, Any]]) -> int:
    removed = 0
    for job in jobs:
        try:
            shutil.rmtree(job["path"])
            removed += 1
        except OSError as exc:
            print(f"  ! failed to remove {job['job_id']}: {exc}", file=sys.stderr)
    return removed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clean stuck and stale jobs out of the unified progress store."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually remove the job directories (default is a dry run)",
    )
    parser.add_argument(
        "--clean-completed",
        action="store_true",
        help="also remove finished jobs older than --max-age-days",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        help=f"age in days past which finished jobs are removed (default {DEFAULT_MAX_AGE_DAYS})",
    )
    parser.add_argument(
        "--max-age-hours",
        type=int,
        default=DEFAULT_MAX_AGE_HOURS,
        help=f"idle hours past which an unfinished job counts as stuck (default {DEFAULT_MAX_AGE_HOURS})",
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("GUAARDVARK_OUTPUT_DIR"),
        help="output directory holding .progress_jobs (default: the configured one)",
    )
    args = parser.parse_args(argv)

    try:
        progress_dir = resolve_progress_dir(args.output_dir)
    except Exception as exc:
        print(f"Could not resolve the progress store: {exc}", file=sys.stderr)
        return 1

    print(f"Progress store: {progress_dir}")
    if not progress_dir.exists():
        print("Store does not exist yet - nothing to clean.")
        print("Cleaned up 0 orphaned jobs")
        return 0

    removable, kept = classify_jobs(
        progress_dir,
        max_age_hours=args.max_age_hours,
        max_age_days=args.max_age_days,
        clean_completed=args.clean_completed,
    )

    for job in removable:
        print(f"  - {job['job_id']} ({job['process_type']}): {job['reason']}")
    print(f"Kept {len(kept)} job(s) still live or too recent to remove.")

    if not args.execute:
        print(
            f"Dry run: {len(removable)} orphaned job(s) would be removed "
            f"(re-run with --execute)."
        )
        print("Cleaned up 0 orphaned jobs")
        return 0

    removed = remove_jobs(removable)
    print(f"Cleaned up {removed} orphaned jobs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
