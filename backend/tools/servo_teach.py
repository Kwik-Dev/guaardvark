#!/usr/bin/env python3
"""Servo teach runner — accumulate TRUTH-LABELED clicks at scale.

Dean's teaching cycle, wave 2: run training-mode sessions on the vision
trainer (Wave-1 truth probe active), spreading target positions so labeled
data covers the whole arena. Composes existing machinery — the agent loop,
TrainerTruthProbe, the archive — this runner only orchestrates.

Position spreading: the trainer's dot only moves on a HIT, so a struggling
agent farms one spot forever (observed: 15 misses on one top-right dot).
When the archive shows N consecutive misses on the same target, the runner
RELOADS the page — fresh random dot, cleared ✕ markers, reset counters.
Reload (not a synthetic click) so the trainer's click counter is never
polluted; the truth probe is delta-based and unaffected across resets.

Multiple short sessions per run: each execute→kill cycle writes its own
servo_logs session file, giving prepare_training_set's session-split real
eval material (a single session yields no held-out set by design).

Usage:
  PYTHONPATH=. GUAARDVARK_MODE=test backend/venv/bin/python -m backend.tools.servo_teach \
      [--sessions 3] [--minutes 4] [--stuck-misses 4] [--port 5000]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "data" / "training" / "knowledge" / "servo_archive.jsonl"
TRAINER_URL = "file:///home/llamax1/LLAMAX8/data/agent/files/vision_trainer.html"
TASK = "click the colored numbered circle in the arena"


def _api(port: int, path: str) -> str:
    return f"http://localhost:{port}/api/agent-control/{path}"


def _agent_active(port: int) -> Optional[bool]:
    try:
        r = requests.get(_api(port, "status"), timeout=5).json()
        return bool(r["status"]["active"])
    except Exception:
        return None


def _archive_truth_rows():
    """(count, rows) of truth-labeled rows currently in the fresh archive."""
    rows = []
    try:
        for line in open(ARCHIVE):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("truth"):
                rows.append(d)
    except FileNotFoundError:
        pass
    return rows


def _reload_trainer() -> bool:
    from backend.services.social_outreach.reddit_outreach import _bidi_navigate
    return bool(_bidi_navigate(TRAINER_URL))


def _consecutive_tail_misses(rows) -> int:
    """Misses at the SAME target position at the tail of the archive."""
    n = 0
    last_pos = None
    for d in reversed(rows):
        t = d["truth"]
        if t.get("true_hit") is True:
            break
        pos = (t.get("target_cx"), t.get("target_cy"))
        if last_pos is None:
            last_pos = pos
        if pos != last_pos:
            break
        n += 1
    return n


def run(sessions: int, minutes: float, stuck_misses: int, port: int) -> int:
    from backend.services.dom_metadata_extractor import DOMMetadataExtractor

    ok, why = DOMMetadataExtractor.get_instance().ensure_agent_firefox()
    print(f"firefox+bidi: {ok} ({why})")
    if not ok and "already" not in str(why).lower():
        return 1
    _reload_trainer()
    time.sleep(1.5)

    start_rows = len(_archive_truth_rows())
    print(f"archive truth rows at start: {start_rows}")

    for s in range(1, sessions + 1):
        if _agent_active(port):
            print("agent busy — aborting")
            return 1
        r = requests.post(_api(port, "execute"), json={"task": TASK, "training_mode": True}, timeout=10)
        if not r.json().get("success"):
            print(f"session {s}: failed to start: {r.text[:120]}")
            return 1
        print(f"session {s}/{sessions} started ({minutes} min)…")

        deadline = time.monotonic() + minutes * 60
        seen = len(_archive_truth_rows())
        while time.monotonic() < deadline:
            time.sleep(10)
            rows = _archive_truth_rows()
            if len(rows) > seen:
                new = rows[seen:]
                seen = len(rows)
                hits = sum(1 for d in new if d["truth"].get("true_hit") is True)
                print(f"  +{len(new)} rows ({hits} hits) — total {len(rows)}")
            # Position spreading: reload on a stuck same-target miss streak.
            # Reload lands in the agent's THINK gap with high probability; a
            # rare mid-click reload just yields one unscored row (dropped by
            # the builders — tri-state protects the dataset).
            if rows and _consecutive_tail_misses(rows) >= stuck_misses:
                print(f"  {stuck_misses}+ consecutive misses on one target — reloading for a fresh position")
                _reload_trainer()
                time.sleep(1.0)

        requests.post(_api(port, "kill"), timeout=10)
        for _ in range(30):
            if _agent_active(port) is False:
                break
            time.sleep(2)
        print(f"session {s} stopped")
        _reload_trainer()  # clean arena + new dot for the next session
        time.sleep(1.0)

    rows = _archive_truth_rows()
    new_rows = rows[start_rows:]
    hits = sum(1 for d in new_rows if d["truth"].get("true_hit") is True)
    misses = sum(1 for d in new_rows if d["truth"].get("true_hit") is False)
    unscored = sum(1 for d in new_rows if d["truth"].get("true_hit") is None)
    errs = [d["true_error_px"] for d in new_rows if d.get("true_error_px") is not None]
    positions = {(d["truth"].get("target_cx"), d["truth"].get("target_cy")) for d in new_rows}
    print("\n===== TEACH RUN SUMMARY =====")
    print(f"new labeled rows: {len(new_rows)}  (hits={hits} misses={misses} unscored={unscored})")
    if errs:
        print(f"mean true error: {sum(errs)/len(errs):.1f}px")
    print(f"distinct target positions: {len(positions)}")
    print(f"archive total truth rows: {len(rows)}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=3)
    ap.add_argument("--minutes", type=float, default=4)
    ap.add_argument("--stuck-misses", type=int, default=4)
    ap.add_argument("--port", type=int, default=5000)
    a = ap.parse_args(argv)
    return run(a.sessions, a.minutes, a.stuck_misses, a.port)


if __name__ == "__main__":
    raise SystemExit(main())
