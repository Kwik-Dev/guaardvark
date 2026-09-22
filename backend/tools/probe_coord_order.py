#!/usr/bin/env python3
"""Measure which axis order a vision model actually points in.

The servo asks every model for ``box_2d: [y1, x1, y2, x2]`` normalised to 1000.
Whether a given model obeys that is not knowable from its name, its family, or
anything Ollama reports — it is an empirical fact about the model, and the only
honest way to get it is to ask the model to point at something whose position we
already know and see which reading of its answer is correct.

Guessing is worse than it sounds. A transposed answer does not fail loudly: it
puts the click at a plausible-looking wrong place, mirrored across the diagonal.
The system then reports a successful click on the wrong thing.

The discriminator is brutally simple. On a board with targets spread off the
diagonal, the wrong axis order costs hundreds of pixels while the right one
costs tens. There is no ambiguous middle.

Writes the winner to data/training/model_coord_probe.json, which the capability
resolver reads. Machine-local, like the servo calibration it sits beside.

Usage:
    GUAARDVARK_MODE=test backend/venv/bin/python -m backend.tools.probe_coord_order \
        --models qwen3-vl:8b-thinking-q8_0 --frames <bench>/manifest.json
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from datetime import datetime
from pathlib import Path
from typing import Optional

STORE = Path(__file__).resolve().parents[2] / "data" / "training" / "model_coord_probe.json"
# How much better the winner must be before we believe it rather than record
# "unknown". A transposition on a 1000px board costs hundreds of pixels; asking
# for a 1.6x margin means a genuinely ambiguous result stays ambiguous instead
# of being resolved by noise.
MIN_RATIO = 1.6


def probe(model: str, manifest: str, grid: int = 1000, max_targets: int = 20) -> dict:
    from backend.tools.eye_bakeoff import load_manifest, eval_frames, MODE_OVERLAYS

    size, frames = load_manifest(manifest)
    # A subset is plenty: the effect being measured is a factor of several, not
    # a few percent, and each target costs an inference.
    trimmed, n = [], 0
    for fr in frames:
        keep = []
        for t in fr["targets"]:
            if n >= max_targets:
                break
            keep.append(t)
            n += 1
        if keep:
            trimmed.append({"image": fr["image"], "targets": keep})
        if n >= max_targets:
            break

    out = {}
    for order in ("yx", "xy"):
        overlay = dict(MODE_OVERLAYS["anchor"])
        overlay.update({"coord_order": order, "internal_width": grid})
        r = eval_frames(model, size, trimmed, overlay)
        rows = [x for x in r["targets"] if x.get("err_x") is not None]
        out[order] = {
            "n": len(rows),
            "median_dist": st.median([x["dist"] for x in rows]) if rows else None,
            "median_x": st.median([abs(x["err_x"]) for x in rows]) if rows else None,
            "median_y": st.median([abs(x["err_y"]) for x in rows]) if rows else None,
        }
        d = out[order]
        print(f"  {model:44s} order={order}  n={d['n']:3d}  "
              f"median dist={d['median_dist']}  |X|={d['median_x']}  |Y|={d['median_y']}")

    a, b = out["yx"]["median_dist"], out["xy"]["median_dist"]
    if not a or not b:
        verdict, conf, why = None, 0.0, "no parseable answers in one or both orders"
    elif a <= b / MIN_RATIO:
        verdict, conf, why = "yx", 0.9, f"yx {a:.0f}px vs xy {b:.0f}px"
    elif b <= a / MIN_RATIO:
        verdict, conf, why = "xy", 0.9, f"xy {b:.0f}px vs yx {a:.0f}px"
    else:
        verdict, conf, why = None, 0.0, (
            f"inconclusive: yx {a:.0f}px vs xy {b:.0f}px, under the {MIN_RATIO}x margin")
    print(f"  -> {model}: {verdict or 'UNKNOWN'}  ({why})")
    return {"order": verdict, "grid": grid, "normalised": True, "confidence": conf,
            "reason": why, "measured": out,
            "probed_at": datetime.now().isoformat(timespec="seconds"),
            "board": Path(manifest).parent.name}


def main(argv: Optional[list] = None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--frames", required=True)
    ap.add_argument("--grid", type=int, default=1000)
    ap.add_argument("--max-targets", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    try:
        store = json.loads(STORE.read_text())
    except Exception:
        store = {}

    for m in [x.strip() for x in args.models.split(",") if x.strip()]:
        res = probe(m, args.frames, args.grid, args.max_targets)
        # A failure is stored too. "This model returns nothing parseable when
        # asked to point" is expensive to discover and worth exactly as much as
        # a success — it is the difference between a model the agent can drive
        # and one it cannot, and the resolver needs to know which.
        store[m] = res
        if res["order"] is None:
            print(f"  recorded {m} as UNUSABLE for pointing: {res['reason']}")
    if args.dry_run:
        print("\ndry-run: nothing written")
        return 0
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(store, indent=2))
    print(f"\nwrote {STORE}")
    try:
        from backend.services.model_capability_resolver import invalidate
        invalidate()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
