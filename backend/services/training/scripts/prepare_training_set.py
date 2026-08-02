#!/usr/bin/env python3
"""Convert raw servo interaction logs into QLoRA training format.

Reads: data/training/servo_logs/*.jsonl
Writes: data/training/datasets/servo_train.jsonl, servo_eval.jsonl

TRUTH-FIRST (Wave 1, 2026-08-01): examples are generated ONLY from records
carrying trainer ground truth (metadata.truth from TrainerTruthProbe). The old
builder's labels were the model's own predictions gated on "xdotool returned 0"
— training on a mirror. Rules now:

  - Coordinate examples: label = (truth.target_cx, target_cy) — where the
    target actually was. Valid for hits AND misses (a missed click still tells
    us exactly where the target sat on that frame).
  - On-target examples: true_hit=True → positive; true_hit=False → honest
    negative with computed direction/distance from the click to the target.
  - true_hit=None (unscored: header/off-arena click) → row DROPPED, never
    counted as a miss.
  - Records with no truth at all (production clicks, pre-Wave-1 rows) → dropped
    for training purposes. No fallback to prediction — that was the poison.
  - Screen size comes from each record's metadata (displays change; 2026-08-01
    it's 1000x1000 — the old 1024 hardcode mislabeled every prompt).
  - Train/eval split is BY SESSION, then shuffled within splits — the old
    shuffle-then-split leaked near-duplicate frames of the same session into
    both sets.
"""

import json
import os
import random
from collections import defaultdict
from pathlib import Path

GUAARDVARK_ROOT = Path(os.environ.get("GUAARDVARK_ROOT", "."))
SERVO_LOGS = GUAARDVARK_ROOT / "data" / "training" / "servo_logs"
DATASETS_DIR = GUAARDVARK_ROOT / "data" / "training" / "datasets"

DEFAULT_SCREEN = (1000, 1000)  # fallback only; real size read per record


def load_servo_logs():
    records = []
    for log_file in sorted(SERVO_LOGS.glob("*.jsonl")):
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                r["_session"] = log_file.stem  # session key for the split
                records.append(r)
    return records


def _truth(r):
    """Return the truth dict for a record, or None."""
    return (r.get("metadata") or {}).get("truth")


def _screen_size(r):
    size = (r.get("metadata") or {}).get("screen_size") or DEFAULT_SCREEN
    return int(size[0]), int(size[1])


def _direction(dx, dy):
    """Compass direction from crosshair toward target (servo NUDGE vocabulary)."""
    horiz = "left" if dx < 0 else "right"
    vert = "up" if dy < 0 else "down"
    if abs(dx) < 12:
        return vert
    if abs(dy) < 12:
        return horiz
    return f"{horiz}_and_{vert}"


def _distance_bucket(dist):
    """Bucket aligned to the servo nudge reflexes (small=10, medium=40, large=80)."""
    if dist < 25:
        return "small"
    if dist < 60:
        return "medium"
    return "large"


def generate_coordinate_examples(records):
    """Where was the target? Label = trainer ground truth, hits AND misses."""
    examples = []
    for r in records:
        t = _truth(r)
        if not t or t.get("target_cx") is None or t.get("target_cy") is None:
            continue
        w, h = _screen_size(r)
        examples.append({
            "image": r["screenshot_path"],
            "conversations": [
                {"role": "user", "content": (
                    f"Screen is {w}x{h}. "
                    f"Where is the {r['target_description']}? "
                    f"Respond with ONLY: {{\"x\": N, \"y\": N}}"
                )},
                {"role": "assistant", "content": json.dumps({
                    "x": int(t["target_cx"]), "y": int(t["target_cy"])
                })},
            ]
        })
    return examples


def generate_correction_examples(records):
    """Honest correction examples from MISSES: crosshair→target vector."""
    examples = []
    for r in records:
        t = _truth(r)
        if not t or t.get("true_hit") is not False:
            continue
        if t.get("target_cx") is None or t.get("target_cy") is None:
            continue
        cx, cy = r.get("crosshair_pos") or (None, None)
        if cx is None:
            continue
        dx, dy = t["target_cx"] - cx, t["target_cy"] - cy
        dist = (dx ** 2 + dy ** 2) ** 0.5
        examples.append({
            "image": r["screenshot_path"],
            "conversations": [
                {"role": "user", "content": (
                    f"The crosshair is at ({cx}, {cy}). "
                    f"How far is it from the {r['target_description']}? "
                    f"Respond with ONLY: {{\"on_target\": false, \"direction\": \"...\", \"distance\": \"...\"}}"
                )},
                {"role": "assistant", "content": json.dumps({
                    "on_target": False,
                    "direction": _direction(dx, dy),
                    "distance": _distance_bucket(dist),
                })},
            ]
        })
    return examples


def generate_on_target_examples(records):
    """Was the click on the target? True labels from the trainer scoreboard."""
    examples = []
    for r in records:
        t = _truth(r)
        if not t or t.get("true_hit") is None:
            continue  # no truth, or unscored click — never guess
        examples.append({
            "image": r["screenshot_path"],
            "conversations": [
                {"role": "user", "content": (
                    f"Is the crosshair directly on the {r['target_description']}? "
                    f"Respond with ONLY: {{\"on_target\": true}} or {{\"on_target\": false, ...}}"
                )},
                {"role": "assistant", "content": json.dumps(
                    {"on_target": True} if t["true_hit"] else {"on_target": False}
                )},
            ]
        })
    return examples


def split_by_session(examples, records_by_image, eval_frac=0.2, seed=7):
    """Split whole SESSIONS into train/eval so near-duplicate frames of one
    session never straddle the boundary (the old leak)."""
    by_session = defaultdict(list)
    for ex in examples:
        session = records_by_image.get(ex["image"], "unknown")
        by_session[session].append(ex)

    sessions = sorted(by_session)
    rng = random.Random(seed)
    rng.shuffle(sessions)
    n_eval = max(1, int(len(sessions) * eval_frac)) if len(sessions) > 1 else 0
    eval_sessions = set(sessions[:n_eval])

    train, eval_set = [], []
    for s, exs in by_session.items():
        (eval_set if s in eval_sessions else train).extend(exs)
    rng.shuffle(train)
    rng.shuffle(eval_set)
    return train, eval_set


def main():
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    records = load_servo_logs()
    with_truth = sum(1 for r in records if _truth(r))
    print(f"Loaded {len(records)} servo records ({with_truth} with ground truth)")

    examples = []
    examples.extend(generate_coordinate_examples(records))
    examples.extend(generate_correction_examples(records))
    examples.extend(generate_on_target_examples(records))
    dropped = len(records) - with_truth
    if dropped:
        print(f"Dropped {dropped} truth-less records (production/pre-Wave-1 rows — no guessing)")

    records_by_image = {r.get("screenshot_path"): r.get("_session", "unknown") for r in records}
    train, eval_set = split_by_session(examples, records_by_image)

    train_path = DATASETS_DIR / "servo_train.jsonl"
    eval_path = DATASETS_DIR / "servo_eval.jsonl"
    for path, data in [(train_path, train), (eval_path, eval_set)]:
        with open(path, "w") as f:
            for ex in data:
                f.write(json.dumps(ex) + "\n")

    print(f"Train: {len(train)} examples -> {train_path}")
    print(f"Eval: {len(eval_set)} examples -> {eval_path}")


if __name__ == "__main__":
    main()
