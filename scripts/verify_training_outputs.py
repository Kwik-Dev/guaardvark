#!/usr/bin/env python3
"""Verify LoRA subject training actually produced REAL, OWNED, FRESH output.

Motivation (2026-06-03): Subject 10 'Serenity' was marked `trained` while its
lora_path pointed at a *different* subject's month-old file (Serenity_Kane_v1)
and lora_version was 0 — a silent "looks done, produced nothing" failure. The
training subsystem records success as a bare status string with no check that
the artifact exists, is non-trivial, belongs to the subject, and is new.

This is the gate that closes that gap. For every Subject it asserts:
  - status=='trained'  => lora_path set, file exists on disk,
                          size >= MIN_REAL_LORA_BYTES (mock stub is ~8 bytes),
                          file mtime is NOT far older than the trained timestamp
                          (older => a borrowed/stale artifact, not this run's),
                          lora_version >= 1.
  - status=='training' => not stuck older than STUCK_TRAINING_HOURS.
  - status in untrained/queued with 0 reference images => cannot ever train (warn).

Exit code: 0 if no FAILs, 1 if any FAIL (so it can gate a run / CI / post-train hook).
Read-only. Run:  backend/venv/bin/python scripts/verify_training_outputs.py [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

MIN_REAL_LORA_BYTES = 100 * 1024          # real LoRA ~tens of MB; mock stub ~8 bytes
STALE_ARTIFACT_SLACK = timedelta(days=1)  # output older than (trained_at - slack) => borrowed
STUCK_TRAINING_HOURS = 6


def _abs(repo_root: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(repo_root, path)


def _as_epoch(dt) -> float | None:
    if not dt:
        return None
    try:
        return dt.timestamp()
    except Exception:
        return None


def check_subject(s, repo_root: str) -> dict:
    """Return {id, name, status, level: PASS|WARN|FAIL, issues: [...]}"""
    status = getattr(s, "training_status", None)
    lp = getattr(s, "lora_path", None)
    ver = getattr(s, "lora_version", None)
    name = getattr(s, "name", "?")
    n_refs = len(getattr(s, "ref_image_paths", None) or [])
    trained_at = getattr(s, "updated_at", None) or getattr(s, "created_at", None)

    issues: list[str] = []
    level = "PASS"

    def fail(msg):
        nonlocal level
        issues.append(msg)
        level = "FAIL"

    def warn(msg):
        nonlocal level
        issues.append(msg)
        if level == "PASS":
            level = "WARN"

    if status == "trained":
        if not lp:
            fail("status=trained but lora_path is empty")
        else:
            abs_lp = _abs(repo_root, lp)
            if not os.path.exists(abs_lp):
                fail(f"lora_path does not exist on disk: {lp}")
            else:
                size = os.path.getsize(abs_lp)
                if size < MIN_REAL_LORA_BYTES:
                    fail(f"output is only {size} B — that's the MOCK stub, not a real LoRA")
                # Borrowed/stale artifact: a real run writes the file AT train time.
                file_mtime = _as_epoch(datetime.fromtimestamp(os.path.getmtime(abs_lp)))
                trained_epoch = _as_epoch(trained_at)
                if file_mtime and trained_epoch:
                    if file_mtime < (trained_epoch - STALE_ARTIFACT_SLACK.total_seconds()):
                        fail(
                            f"output file is OLDER than the trained timestamp "
                            f"(file={datetime.fromtimestamp(file_mtime):%Y-%m-%d}, "
                            f"trained={datetime.fromtimestamp(trained_epoch):%Y-%m-%d}) "
                            f"-> borrowed/stale artifact, not produced by this run"
                        )
        if ver in (0, None):
            warn(f"lora_version={ver} on a trained subject (real success path sets >= 1)")

    elif status == "training":
        trained_epoch = _as_epoch(trained_at)
        if trained_epoch is not None:
            age = datetime.now().timestamp() - trained_epoch
            if age > STUCK_TRAINING_HOURS * 3600:
                fail(f"stuck in 'training' for {age/3600:.1f}h (>{STUCK_TRAINING_HOURS}h)")

    if status in (None, "untrained", "queued") and n_refs == 0:
        warn("0 reference images -> cannot be trained (real trainer fails 'no reference images provided')")

    return {
        "id": getattr(s, "id", None),
        "name": name,
        "status": status,
        "lora_path": lp,
        "lora_version": ver,
        "ref_images": n_refs,
        "level": level,
        "issues": issues,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify LoRA training produced real owned output.")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()

    repo_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

    # This is a read-only verifier — silence the app's boot-time INFO chatter so the
    # report is the only thing on stdout (keeps it usable as a gate / in a pipe).
    import logging
    logging.disable(logging.WARNING)

    from backend.app import create_app
    from backend.models import Subject

    app = create_app()
    with app.app_context():
        rows = [check_subject(s, repo_root) for s in Subject.query.order_by(Subject.id).all()]

    fails = [r for r in rows if r["level"] == "FAIL"]
    warns = [r for r in rows if r["level"] == "WARN"]

    if args.json:
        print(json.dumps({"rows": rows, "fail": len(fails), "warn": len(warns)}, indent=2, default=str))
    else:
        icon = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌"}
        for r in rows:
            print(f"{icon[r['level']]} [{r['id']:>3}] {r['status']:<10} {r['name']!r}")
            for iss in r["issues"]:
                print(f"        - {iss}")
        print(f"\nSummary: {len(rows)} subjects | {len(fails)} FAIL | {len(warns)} WARN")
        if fails:
            print("RESULT: FAIL — training output is not what the status claims. See ❌ above.")
        else:
            print("RESULT: OK — every 'trained' subject has a real, owned, fresh LoRA.")

    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
