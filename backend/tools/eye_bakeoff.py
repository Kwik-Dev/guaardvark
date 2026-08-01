#!/usr/bin/env python3
"""Agentic EYE bake-off — rank vision models by click-pointing accuracy.

Runs each candidate through the REAL servo two-pass path
(ServoController._estimate_coordinates) on a BLIND square calibration image that
matches the production 1000x1000 agent display — the conditions gemma4 is
calibrated for. Measures pixel error vs. known ground-truth circle centers.

This is the repeatable agentic eval MASTER_TASKS L2411 called for. Static image
+ fake screen, so it needs only Ollama up (no :99 display, no clicking).

Usage:
    GUAARDVARK_MODE=test backend/venv/bin/python -m backend.tools.eye_bakeoff
    ... --models gemma4:e4b,minicpm-v4.5:latest --size 1000 --out results.json
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Optional

from PIL import Image, ImageDraw

# Blind targets: (label used in the prompt, RGB fill, ground-truth center as a
# FRACTION of the canvas so it scales with --size).
TARGETS = [
    ("red circle", (220, 30, 30), (0.20, 0.20)),
    ("blue circle", (30, 30, 220), (0.80, 0.20)),
    ("green circle", (30, 160, 40), (0.50, 0.50)),
    ("orange circle", (240, 150, 20), (0.20, 0.80)),
    ("purple circle", (150, 30, 150), (0.80, 0.80)),
]

DEFAULT_MODELS = [
    "gemma4:e4b",
    "minicpm-v4.5:latest",
    "qwen2.5vl:7b-q4_K_M",
    "qwen3-vl:8b",
]

# Uniform box_2d-normalized-1000 convention (gemma4's working config) applied to
# every candidate so the test is apples-to-apples. Models that use a different
# convention will show high error → flagged as needing per-model calibration.
BASE_VISION_CONFIG = {
    "has_vision": True,
    "vision_model": None,
    "internal_width": 1000,
    "scale_x": 1.0,
    "scale_y": 1.0,
    "offset_x": 0,
    "offset_y": 0,
    "native_pointing": True,
    "coord_order": "yx",
    "source": "eye_bakeoff_uniform",
}


class _FakeScreen:
    """Minimal screen for ServoController — only screen_size() is used off the
    static-image path (no capture, no click)."""

    def __init__(self, w: int, h: int):
        self._w, self._h = w, h

    def screen_size(self):
        return (self._w, self._h)


def make_calibration_image(size: int = 1000) -> tuple[Image.Image, list]:
    """Blind square image: colored circles on white, NO coordinate labels."""
    img = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(img)
    truth = []
    r = max(16, size // 45)
    for label, rgb, (fx, fy) in TARGETS:
        cx, cy = int(fx * size), int(fy * size)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=rgb, outline="black", width=2)
        truth.append((label, (cx, cy)))
    return img, truth


def eval_model(model: str, image: Image.Image, truth: list) -> dict:
    from backend.utils.vision_analyzer import VisionAnalyzer
    from backend.services.servo_controller import ServoController

    analyzer = VisionAnalyzer()
    analyzer.default_model = model
    screen = _FakeScreen(*image.size)
    servo = ServoController(screen, analyzer, vision_config=dict(BASE_VISION_CONFIG))

    rows, errs, t0 = [], [], time.time()
    for label, (gx, gy) in truth:
        try:
            coords = servo._estimate_coordinates(image, label)
        except Exception as e:  # noqa: BLE001
            rows.append({"target": label, "error": None, "reason": f"exc:{e}"})
            continue
        if not coords:
            rows.append({"target": label, "error": None,
                         "reason": servo._last_failure_reason or "no_coords"})
            continue
        err = ((coords[0] - gx) ** 2 + (coords[1] - gy) ** 2) ** 0.5
        errs.append(err)
        rows.append({"target": label, "pred": list(coords), "gt": [gx, gy],
                     "error": round(err, 1)})
    return {
        "model": model,
        "targets": rows,
        "hits": len(errs),
        "total": len(truth),
        "mean_error": round(sum(errs) / len(errs), 1) if errs else None,
        "max_error": round(max(errs), 1) if errs else None,
        "seconds": round(time.time() - t0, 1),
    }


def main(argv: Optional[list] = None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--size", type=int, default=1000)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    image, truth = make_calibration_image(args.size)
    hit_radius = max(16, args.size // 45)  # a "hit" lands within the circle

    results = []
    for m in models:
        print(f"\n===== {m} =====")
        r = eval_model(m, image, truth)
        for row in r["targets"]:
            if row.get("error") is not None:
                mark = "HIT " if row["error"] <= hit_radius else "miss"
                print(f"  {row['target']:14s} {mark} pred={tuple(row['pred'])} "
                      f"gt={tuple(row['gt'])} err={row['error']}px")
            else:
                print(f"  {row['target']:14s} FAIL {row.get('reason')}")
        print(f"  -> {r['hits']}/{r['total']} parsed  mean={r['mean_error']}px  "
              f"max={r['max_error']}px  ({r['seconds']}s)")
        results.append(r)

    ranked = sorted([r for r in results if r["mean_error"] is not None],
                    key=lambda r: r["mean_error"])
    print("\n===== RANKING (lower mean error = better pointer) =====")
    for i, r in enumerate(ranked, 1):
        print(f"  {i}. {r['model']:26s} mean={r['mean_error']}px  hits={r['hits']}/{r['total']}")
    for r in results:
        if r["mean_error"] is None:
            print(f"  --. {r['model']:26s} NO PARSEABLE OUTPUT (needs calibration/handling)")

    if args.out:
        with open(args.out, "w") as f:
            json.dump({"hit_radius_px": hit_radius, "results": results}, f, indent=2)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
