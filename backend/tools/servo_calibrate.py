#!/usr/bin/env python3
"""Servo aim calibration — fit the eye's raw anchor bias against DOM truth.

Runs the REAL servo estimate path (calibration disabled, so we measure the raw
eye) against the vision trainer page, whose DOM knows every target's exact
screen position. Fits a per-axis linear map raw ≈ a + b·truth with outlier
trimming, sanity-checks the slopes, and saves it to the Tier-1.5 runtime store
(data/training/servo_calibration.json). The ServoController inverts the map on
every anchor from then on.

Prereq: agent display :99 up, BiDi Firefox on :9222 showing vision_trainer.html
(the runner navigates there itself), Ollama serving the eye model.

Usage:
  PYTHONPATH=. GUAARDVARK_MODE=test backend/venv/bin/python -m backend.tools.servo_calibrate \
      [--samples 12] [--model gemma4:e4b] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import time
from typing import List, Optional, Tuple

TRAINER_URL = "file:///home/llamax1/LLAMAX8/data/agent/files/vision_trainer.html"

TRUTH_JS = """(() => {
  const t = document.querySelector('.target');
  if (!t) return "none";
  const r = t.getBoundingClientRect();
  return JSON.stringify({cx: r.left + r.width/2 + window.mozInnerScreenX,
                         cy: r.top + r.height/2 + window.mozInnerScreenY,
                         label: t.textContent.trim()});
})()"""

RESPAWN_JS = "document.querySelector('.target') && document.querySelector('.target').click()"


def _bidi(expr: str):
    import websocket as _ws
    ws = _ws.create_connection("ws://localhost:9222/session", timeout=10, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "session.new", "params": {"capabilities": {}}}))
        json.loads(ws.recv())
        ws.send(json.dumps({"id": 2, "method": "browsingContext.getTree", "params": {}}))
        ctx = json.loads(ws.recv())["result"]["contexts"][0]["context"]
        ws.send(json.dumps({"id": 3, "method": "script.evaluate", "params": {
            "expression": expr, "target": {"context": ctx}, "awaitPromise": False}}))
        r = json.loads(ws.recv())
        ws.send(json.dumps({"id": 4, "method": "session.end", "params": {}}))
        return r["result"]["result"].get("value")
    finally:
        ws.close()


def fit_axis(truths: List[float], raws: List[float], mad_k: float = 3.5) -> Tuple[float, float, int]:
    """Least-squares raw = a + b·truth with a robust (MAD) outlier-trim pass.

    MAD, not σ: a single wild outlier inflates σ enough to hide itself, while
    the median absolute deviation stays anchored to the inliers.
    Returns (a, b, n_used). Pure function — unit-testable without a display.
    """
    def _lsq(ts, rs):
        n = len(ts)
        tm, rm = sum(ts) / n, sum(rs) / n
        denom = sum((t - tm) ** 2 for t in ts) or 1e-9
        b = sum((t - tm) * (r - rm) for t, r in zip(ts, rs)) / denom
        return rm - b * tm, b

    import statistics
    # Initial fit via Theil–Sen (median of pairwise slopes): a single wild
    # sample has LEVERAGE under least-squares — it tilts the line toward
    # itself so residual-trimming can't see it. The pairwise-slope median
    # shrugs it off entirely.
    n = len(truths)
    slopes = [
        (raws[j] - raws[i]) / (truths[j] - truths[i])
        for i in range(n) for j in range(i + 1, n)
        if abs(truths[j] - truths[i]) > 1e-9
    ]
    b = statistics.median(slopes)
    a = statistics.median(r - b * t for t, r in zip(truths, raws))

    residuals = [r - (a + b * t) for t, r in zip(truths, raws)]
    med = statistics.median(residuals)
    mad = statistics.median(abs(e - med) for e in residuals) or 1e-9
    cutoff = mad_k * 1.4826 * mad
    kept = [(t, r) for t, r, e in zip(truths, raws, residuals) if abs(e - med) <= cutoff]
    if len(kept) >= 3:
        a, b = _lsq([k[0] for k in kept], [k[1] for k in kept])
    return a, b, len(kept)


def collect_samples(model: str, n: int, settle_s: float = 0.8):
    """Drive the raw (uncalibrated) servo path; pair anchors with DOM truth."""
    import re
    from backend.services.local_screen_backend import LocalScreenBackend
    from backend.utils.vision_analyzer import VisionAnalyzer
    from backend.services.servo_controller import ServoController
    from backend.services.servo_knowledge_store import get_vision_config

    screen = LocalScreenBackend()
    analyzer = VisionAnalyzer(default_model=model)
    cfg = dict(get_vision_config(model))
    cfg["disable_calibration"] = True  # measure the RAW eye
    servo = ServoController(screen, analyzer, vision_config=cfg)

    samples = []
    for i in range(n):
        raw_truth = _bidi(TRUTH_JS)
        if raw_truth == "none":
            break
        truth = json.loads(raw_truth)
        img, _ = screen.capture()
        servo._estimate_coordinates(img, f"colored circle with number {truth['label']}")
        # The correction targets the ANCHOR (it places the refine crop) — parse
        # the raw anchor box from the verbatim telemetry, not the refined final.
        m = re.search(r'anchor:.*?"box_2d":\s*\[([\d\s,.-]+)\]', servo._last_raw_response or "")
        if m:
            y1, x1, y2, x2 = [float(v) for v in m.group(1).split(",")[:4]]
            ax = (x1 + x2) / 2 / 1000 * servo.screen_w
            ay = (y1 + y2) / 2 / 1000 * servo.screen_h
            samples.append((truth["cx"], truth["cy"], ax, ay))
            print(f"  #{len(samples):2d} truth=({truth['cx']:4.0f},{truth['cy']:4.0f}) "
                  f"anchor=({ax:4.0f},{ay:4.0f}) err=({ax-truth['cx']:+4.0f},{ay-truth['cy']:+4.0f})")
        else:
            print(f"  sample {i+1}: no parseable anchor — skipped")
        _bidi(RESPAWN_JS)  # move the dot for the next sample
        time.sleep(settle_s)
    return samples, servo.screen_w, servo.screen_h


def main(argv: Optional[list] = None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=12)
    ap.add_argument("--model", default="gemma4:e4b")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from backend.services.servo_knowledge_store import (
        CALIBRATION_SLOPE_MIN, CALIBRATION_SLOPE_MAX, save_servo_calibration,
    )
    from backend.services.social_outreach.reddit_outreach import _bidi_navigate

    print(f"navigating agent Firefox to trainer: {_bidi_navigate(TRAINER_URL)}")
    time.sleep(1.5)

    print(f"collecting {args.samples} samples with model {args.model} (raw eye)…")
    samples, w, h = collect_samples(args.model, args.samples)
    if len(samples) < 8:
        print(f"ABORT: only {len(samples)} usable samples (<8) — not enough for a trustworthy fit")
        return 1

    tx = [s[0] for s in samples]; ty = [s[1] for s in samples]
    rx = [s[2] for s in samples]; ry = [s[3] for s in samples]
    a_x, b_x, n_x = fit_axis(tx, rx)
    a_y, b_y, n_y = fit_axis(ty, ry)

    before = [((r - t) ** 2 + (r2 - t2) ** 2) ** 0.5 for t, t2, r, r2 in samples]
    after = [(((( (r - a_x) / b_x) - t) ** 2) + ((((r2 - a_y) / b_y) - t2) ** 2)) ** 0.5
             for t, t2, r, r2 in samples]
    print(f"\nX: raw = {a_x:+.1f} + {b_x:.4f}·truth  (n={n_x})")
    print(f"Y: raw = {a_y:+.1f} + {b_y:.4f}·truth  (n={n_y})")
    print(f"anchor error  before: {sum(before)/len(before):.1f}px   after-correction: {sum(after)/len(after):.1f}px")

    for b in (b_x, b_y):
        if not (CALIBRATION_SLOPE_MIN <= abs(b) <= CALIBRATION_SLOPE_MAX):
            print(f"ABORT: slope {b:.3f} outside sanity bounds [{CALIBRATION_SLOPE_MIN}, {CALIBRATION_SLOPE_MAX}] — not saving")
            return 1

    if args.dry_run:
        print("dry-run: not saved")
        return 0

    key = save_servo_calibration(args.model, w, h, {
        "a_x": round(a_x, 2), "b_x": round(b_x, 4),
        "a_y": round(a_y, 2), "b_y": round(b_y, 4),
        "samples": len(samples),
        "mean_err_before_px": round(sum(before) / len(before), 1),
        "mean_err_after_px": round(sum(after) / len(after), 1),
        "fitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "trainer_dom_truth",
    })
    print(f"SAVED calibration → {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
