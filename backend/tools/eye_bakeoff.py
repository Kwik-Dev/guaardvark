#!/usr/bin/env python3
"""Agentic EYE bake-off — rank vision models by click-pointing accuracy.

Two ways to feed it:

* **Synthetic** (default) — draws a blind square calibration image and scores
  against the circles it just drew. Needs only Ollama up.
* **Replay** (``--frames manifest.json``) — replays real captured frames with
  known target centres. This is how a change to the servo is proved: same
  pixels, same truth, different code.

Either way it drives the REAL ``ServoController._estimate_coordinates`` against
a fake screen, so what it measures is the shipping path and not a reimplementation
of it.

X and Y are scored SEPARATELY and that is the whole point. On the 2026-09-21
blind-calibration session the eye's median absolute X error was 6px while its
Y error was 91px — a single Euclidean number reports "91px, bad model" and
hides the fact that one axis is near-perfect and the other has a linear,
correctable bias.

Usage:
    GUAARDVARK_MODE=test backend/venv/bin/python -m backend.tools.eye_bakeoff
    ... --frames bench/manifest.json --mode anchor --mode pipeline --out r.json
    ... --derive-truth colour --frames-glob 'data/training/screenshots/2026*.webp'
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import os
import statistics as _st
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

# No coord_order here: the servo then resolves each model's measured convention
# exactly as the agent does, so a score is the score the agent would get. A
# hard-coded "yx" read every model in gemma4's order, and qwen3.6:27b, which
# points within 2px, scored 118px with its axes swapped (2026-09-24). Pass
# --coord-order to force one order across every model.
#
# disable_calibration defaults ON here so the bake-off measures the RAW eye and
# gives the same answer on a box that happens to have a fit on disk as on one
# that doesn't. The `calibrated` and `full` modes opt it back in.
BASE_VISION_CONFIG = {
    "has_vision": True,
    "vision_model": None,
    "internal_width": 1000,
    "scale_x": 1.0,
    "scale_y": 1.0,
    "offset_x": 0,
    "offset_y": 0,
    "native_pointing": True,
    "source": "eye_bakeoff_uniform",
    "disable_calibration": True,
}

# Each mode is a vision_config overlay, so the three configurations we care about
# are comparable in one run instead of being three separate git states.
MODE_OVERLAYS = {
    "anchor":     {"disable_refine": True,  "disable_calibration": True},
    "pipeline":   {"disable_refine": False, "disable_calibration": True},
    "calibrated": {"disable_refine": True,  "disable_calibration": False},
    "full":       {"disable_refine": False, "disable_calibration": False},
}
DEFAULT_MODES = ["anchor", "pipeline"]


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


# --------------------------------------------------------------------------
# Ground truth from pixels
# --------------------------------------------------------------------------

# Hue buckets in degrees. These are not arbitrary: on the 2026-09-21 page the
# dots sit at red 0, orange 39, green 120, blue 240, purple 300, and the click
# marker is a red core (hue 0-17) inside a yellow ring (hue 50-70) whose
# anti-aliased edge lands at hue 16-31. So the marker contaminates the `red`
# and `orange` buckets and nothing else — which is exactly what the purity
# guard below detects without being told.
HUE_BUCKETS = [
    ("red", 345.0, 15.0),       # wraps 0
    ("orange", 15.0, 50.0),
    ("yellow", 50.0, 75.0),
    ("green", 75.0, 170.0),
    ("blue", 170.0, 265.0),
    ("purple", 265.0, 345.0),
]


def _hsv(img: Image.Image):
    import numpy as np
    a = np.asarray(img.convert("RGB")).astype("float32") / 255.0
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx, mn = a.max(-1), a.min(-1)
    d = mx - mn
    h = np.zeros_like(mx)
    m = d > 1e-6
    rm, gm, bm = (mx == r) & m, (mx == g) & m, (mx == b) & m
    h[rm] = ((g - b)[rm] / d[rm]) % 6.0
    h[gm] = ((b - r)[gm] / d[gm]) + 2.0
    h[bm] = ((r - g)[bm] / d[bm]) + 4.0
    h *= 60.0
    s = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0.0)
    return h, s, mx


def colour_blobs(img: Image.Image, min_sat: float = 0.45, min_val: float = 0.25) -> dict:
    """Centroid, pixel count and radius of every saturated hue bucket present."""
    import numpy as np
    h, s, v = _hsv(img)
    sat = (s >= min_sat) & (v >= min_val)
    out = {}
    for name, lo, hi in HUE_BUCKETS:
        m = ((h >= lo) | (h < hi)) if lo > hi else ((h >= lo) & (h < hi))
        m = m & sat
        n = int(m.sum())
        if n < 40:
            continue
        ys, xs = np.nonzero(m)
        cx, cy = float(xs.mean()), float(ys.mean())
        dist = np.hypot(xs - cx, ys - cy)
        out[name] = {
            "px": n,
            "cx": cx,
            "cy": cy,
            "r": float((n / 3.141592653589793) ** 0.5),
            "outlier_frac": float((dist > 3.0 * (n / 3.141592653589793) ** 0.5).mean()),
        }
    return out


def _reading_order(items):
    """Sort (name, blob) into reading order, tolerant of small row wobble."""
    rows, cur = [], []
    for it in sorted(items, key=lambda kv: kv[1]["cy"]):
        if cur and abs(it[1]["cy"] - cur[-1][1]["cy"]) > 60:
            rows.append(cur)
            cur = []
        cur.append(it)
    if cur:
        rows.append(cur)
    ordered = []
    for row in rows:
        ordered.extend(sorted(row, key=lambda kv: kv[1]["cx"]))
    return ordered


def derive_truth_colour(paths: list, template: str = "{colour} dot {letter}",
                        pct_tol: float = 0.15, centroid_tol: float = 10.0,
                        outlier_tol: float = 0.05) -> dict:
    """Build a manifest from captured frames by colour-blob centroid.

    The purity guard is the load-bearing part. A colour class is marked
    ``clean: false`` when, relative to the FIRST frame in the set, its pixel
    count moved more than ``pct_tol``, or its centroid moved more than
    ``centroid_tol`` px, or more than ``outlier_tol`` of its pixels sit beyond
    3r from the centroid. Frame 1 of a session is clean by construction (no
    clicks have landed yet), so it is the right reference. Without this, a page
    that leaves coloured click markers quietly rewrites its own ground truth and
    every number downstream is measured against a moving target.
    """
    if not paths:
        raise SystemExit("no frames given")
    first = colour_blobs(Image.open(paths[0]))
    ordered = _reading_order(list(first.items()))
    letters = {name: chr(ord("A") + i) for i, (name, _) in enumerate(ordered)}

    size = Image.open(paths[0]).size
    frames = []
    for p in paths:
        img = Image.open(p)
        blobs = colour_blobs(img)
        targets = []
        for name, _ in ordered:
            b = blobs.get(name)
            if not b:
                continue
            ref = first[name]
            dmoved = ((b["cx"] - ref["cx"]) ** 2 + (b["cy"] - ref["cy"]) ** 2) ** 0.5
            dpct = abs(b["px"] - ref["px"]) / max(1.0, ref["px"])
            reasons = []
            if dpct > pct_tol:
                reasons.append(f"px{dpct * 100:.0f}%")
            if dmoved > centroid_tol:
                reasons.append(f"centroid{dmoved:.0f}px")
            if b["outlier_frac"] > outlier_tol:
                reasons.append(f"scatter{b['outlier_frac'] * 100:.0f}%")
            targets.append({
                "prompt": template.format(colour=name, letter=letters[name]),
                "colour": name,
                # Truth is always the FIRST frame's centroid. A later frame's
                # centroid may have been dragged by markers; the dot did not move.
                "cx": int(round(ref["cx"])),
                "cy": int(round(ref["cy"])),
                "r": max(12, int(round(ref["r"]))),
                "clean": not reasons,
                "impure": ",".join(reasons),
            })
        frames.append({"image": os.path.relpath(p), "targets": targets})
    return {"display": list(size), "source": f"derive_truth_colour n={len(paths)}",
            "frames": frames}


def load_manifest(path: str):
    with open(path) as f:
        man = json.load(f)
    base = os.path.dirname(os.path.abspath(path))
    frames = []
    for fr in man["frames"]:
        img = fr["image"]
        if not os.path.isabs(img):
            cand = os.path.join(base, img)
            img = cand if os.path.exists(cand) else img
        frames.append({"image": img, "targets": fr["targets"]})
    return tuple(man.get("display") or Image.open(frames[0]["image"]).size), frames


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def _stats(vals):
    if not vals:
        return None
    a = sorted(vals)
    return {
        "n": len(a),
        "mean": round(_st.mean(a), 1),
        "median": round(_st.median(a), 1),
        "med_abs": round(_st.median([abs(x) for x in a]), 1),
        "p90_abs": round(sorted(abs(x) for x in a)[int(0.9 * (len(a) - 1))], 1),
    }


def score(rows: list, hit_radius: float) -> dict:
    """Per-axis first, distance second. See the module docstring for why."""
    def _for(subset):
        ok = [r for r in subset if r.get("err_x") is not None]
        if not ok:
            return {"n": 0}
        dist = [r["dist"] for r in ok]
        return {
            "n": len(ok),
            "x": _stats([r["err_x"] for r in ok]),
            "y": _stats([r["err_y"] for r in ok]),
            "dist_median": round(_st.median(dist), 1),
            "dist_mean": round(_st.mean(dist), 1),
            "hits": sum(1 for d in dist if d <= hit_radius),
            "hit_rate": round(sum(1 for d in dist if d <= hit_radius) / len(ok), 3),
        }
    clean = [r for r in rows if r.get("clean", True)]
    return {"all": _for(rows), "clean": _for(clean),
            "polluted_excluded": len(rows) - len(clean),
            "failed": sum(1 for r in rows if r.get("err_x") is None)}


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------

def _servo_for(model: str, size, overlay: dict):
    from backend.utils.vision_analyzer import VisionAnalyzer
    from backend.services.servo_controller import ServoController
    analyzer = VisionAnalyzer()
    analyzer.default_model = model
    cfg = dict(BASE_VISION_CONFIG)
    cfg.update(overlay or {})
    return ServoController(_FakeScreen(*size), analyzer, vision_config=cfg)


def sighted_models() -> list:
    from backend.services.model_capability_resolver import _installed, sees_natively
    return [m for m in _installed() if sees_natively(m)]


def eval_model(model: str, image: Image.Image, truth: list, overlay: dict = None) -> dict:
    """Synthetic single-image path. Kept for the default bake-off and its tests."""
    frames = [{"image": image,
               "targets": [{"prompt": lbl, "cx": cx, "cy": cy, "r": 22, "clean": True}
                           for lbl, (cx, cy) in truth]}]
    r = eval_frames(model, image.size, frames, overlay)
    r["mean_error"] = (r["score"]["all"] or {}).get("dist_mean")
    r["max_error"] = max((row["dist"] for row in r["targets"] if row.get("dist") is not None),
                         default=None)
    r["hits"] = r["score"]["all"].get("n", 0)
    r["total"] = len(truth)
    return r


def eval_frames(model: str, size, frames: list, overlay: dict = None,
                hit_radius: Optional[float] = None) -> dict:
    servo = _servo_for(model, size, overlay)
    rows, t0 = [], time.time()
    radii = []
    for fr in frames:
        img = fr["image"] if isinstance(fr["image"], Image.Image) else Image.open(fr["image"])
        img = img.convert("RGB")
        name = fr["image"] if isinstance(fr["image"], str) else "<synthetic>"
        for t in fr["targets"]:
            radii.append(t.get("r", 22))
            row = {"frame": os.path.basename(str(name)), "target": t["prompt"],
                   "gt": [t["cx"], t["cy"]], "clean": t.get("clean", True),
                   "impure": t.get("impure", "")}
            try:
                coords = servo._estimate_coordinates(img, t["prompt"])
            except Exception as e:  # noqa: BLE001
                row.update({"err_x": None, "reason": f"exc:{e}"})
                rows.append(row)
                continue
            if not coords:
                row.update({"err_x": None,
                            "reason": servo._last_failure_reason or "no_coords"})
                rows.append(row)
                continue
            ex, ey = coords[0] - t["cx"], coords[1] - t["cy"]
            row.update({"pred": list(coords), "err_x": ex, "err_y": ey,
                        "dist": round((ex * ex + ey * ey) ** 0.5, 1),
                        "parse_path": getattr(servo, "_last_parse_path", "")})
            rows.append(row)
    hr = hit_radius if hit_radius is not None else (sum(radii) / len(radii) if radii else 22)
    vc = getattr(servo, "_vision_config", None) or {}
    return {"model": model, "targets": rows, "score": score(rows, hr),
            "hit_radius_px": round(hr, 1), "seconds": round(time.time() - t0, 1),
            # What the servo actually read with, forced or resolved.
            "coord_order": vc.get("coord_order"),
            "style": vc.get("coord_style") or "google_box2d"}


def _print_axis(tag, s):
    if not s or not s.get("n"):
        print(f"  {tag:9s} (no parseable targets)")
        return
    x, y = s["x"], s["y"]
    print(f"  {tag:9s} n={s['n']:3d}  "
          f"X med|e|={x['med_abs']:6.1f} (signed med {x['median']:+6.1f}, p90 {x['p90_abs']:6.1f})  "
          f"Y med|e|={y['med_abs']:6.1f} (signed med {y['median']:+6.1f}, p90 {y['p90_abs']:6.1f})")
    print(f"  {'':9s}      dist median={s['dist_median']:6.1f}  mean={s['dist_mean']:6.1f}  "
          f"hit-rate={s['hit_rate']:.2f} ({s['hits']}/{s['n']})")


def main(argv: Optional[list] = None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--size", type=int, default=1000)
    ap.add_argument("--frames", default="", help="manifest.json for replay mode")
    ap.add_argument("--mode", action="append", choices=sorted(MODE_OVERLAYS),
                    help="repeatable; default: anchor + pipeline")
    ap.add_argument("--derive-truth", choices=["colour"], default="",
                    help="build a manifest from --frames-glob and exit")
    ap.add_argument("--frames-glob", default="")
    ap.add_argument("--prompt-template", default="{colour} dot {letter}")
    ap.add_argument("--coord-order", choices=["xy", "yx"], default="",
                    help="force the axis order instead of using the model's config — "
                         "the coordinate probe runs both and compares")
    ap.add_argument("--grid", type=int, default=0,
                    help="force the normalisation denominator (0 = use the config)")
    ap.add_argument("--out", default="")
    ap.add_argument("--all-vision", action="store_true",
                    help="every installed model that Ollama says can see")
    ap.add_argument("--best-dialect", action="store_true",
                    help="probe an unmeasured model's dialect first, then run in it")
    ap.add_argument("--record", action="store_true",
                    help="write each anchor-mode score into the measurement store")
    ap.add_argument("--max-targets", type=int, default=12, help="for --best-dialect probing")
    args = ap.parse_args(argv)

    if args.derive_truth:
        paths = sorted(_glob.glob(args.frames_glob))
        man = derive_truth_colour(paths, template=args.prompt_template)
        dest = args.out or "manifest.json"
        with open(dest, "w") as f:
            json.dump(man, f, indent=2)
        t0 = man["frames"][0]["targets"]
        print(f"wrote {dest}: {len(man['frames'])} frames x {len(t0)} targets, "
              f"display={man['display']}")
        for t in t0:
            print(f"  {t['prompt']:16s} truth=({t['cx']},{t['cy']}) r={t['r']}")
        impure = {}
        for fr in man["frames"]:
            for t in fr["targets"]:
                if not t["clean"]:
                    impure.setdefault(t["prompt"], set()).add(t["impure"])
        print("  purity guard flagged:",
              ", ".join(f"{k} [{'; '.join(sorted(v))}]" for k, v in impure.items()) or "nothing")
        return

    modes = args.mode or DEFAULT_MODES
    models = sighted_models() if args.all_vision else \
        [m.strip() for m in args.models.split(",") if m.strip()]
    if args.all_vision:
        print(f"sighted models per Ollama: {', '.join(models)}")

    if args.frames:
        size, frames = load_manifest(args.frames)
        hit_radius = None
        label = f"replay {os.path.basename(args.frames)}"
    else:
        image, truth = make_calibration_image(args.size)
        frames = [{"image": image,
                   "targets": [{"prompt": lbl, "cx": cx, "cy": cy,
                                "r": max(16, args.size // 45), "clean": True}
                               for lbl, (cx, cy) in truth]}]
        size = (args.size, args.size)
        hit_radius = max(16, args.size // 45)
        label = f"synthetic {args.size}x{args.size}"

    print(f"=== {label} | modes: {', '.join(modes)} ===")
    results = []
    for m in models:
        for mode in modes:
            print(f"\n===== {m}  [{mode}] =====")
            overlay = dict(MODE_OVERLAYS[mode])
            if args.best_dialect and args.frames:
                from backend.services.model_capability_resolver import (
                    coords_for, MEASURED_CONFIDENCE, invalidate)
                from backend.services.model_capability_data import style_overlay
                conv = coords_for(m, tuple(size))
                if conv.confidence < MEASURED_CONFIDENCE or conv.order is None:
                    from backend.tools.probe_coord_order import probe
                    from backend.services.servo_knowledge_store import record_measurement
                    print(f"  probing {m}'s dialect first (convention was {conv.source})")
                    res = probe(m, args.frames, args.max_targets)
                    record_measurement(m, size[0], size[1], "coords", res["coords"])
                    if res["accuracy"]:
                        record_measurement(m, size[0], size[1], "accuracy", res["accuracy"])
                    invalidate(m)
                    conv = coords_for(m, tuple(size))
                if conv.order is None:
                    print(f"  {m}: no usable dialect — skipped")
                    continue
                overlay.update(style_overlay(conv.style, conv.order))
                overlay["min_num_predict"] = conv.min_num_predict
            if args.coord_order:
                overlay["coord_order"] = args.coord_order
            if args.grid:
                overlay["internal_width"] = args.grid
            r = eval_frames(m, size, frames, overlay, hit_radius)
            r["mode"] = mode
            if args.record and mode == "anchor" and args.frames:
                from backend.services.servo_knowledge_store import record_measurement
                sc = r["score"]["clean"] if r["score"]["clean"].get("n") else r["score"]["all"]
                if sc.get("n"):
                    record_measurement(m, size[0], size[1], "accuracy", {
                        "median_px": sc["dist_median"], "median_x": sc["x"]["med_abs"],
                        "median_y": sc["y"]["med_abs"], "hit_rate": sc["hit_rate"], "n": sc["n"],
                        "mode": mode, "style": r["style"],
                        "board": os.path.basename(os.path.dirname(os.path.abspath(args.frames))),
                        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    })
                    print(f"  recorded accuracy for {m}: {sc['dist_median']}px over {sc['n']}")
            for row in r["targets"]:
                if row.get("err_x") is None:
                    print(f"  {row['target']:16s} FAIL {row.get('reason')}")
            _print_axis("all", r["score"]["all"])
            if r["score"]["polluted_excluded"]:
                _print_axis("clean", r["score"]["clean"])
                print(f"  {'':9s}      ({r['score']['polluted_excluded']} polluted targets held out)")
            print(f"  {'':9s}      {r['seconds']}s")
            results.append(r)

    print("\n===== RANKING (clean median distance; lower is better) =====")
    def _key(r):
        s = r["score"]["clean"] if r["score"]["clean"].get("n") else r["score"]["all"]
        return s.get("dist_median", 1e9)
    for i, r in enumerate(sorted(results, key=_key), 1):
        s = r["score"]["clean"] if r["score"]["clean"].get("n") else r["score"]["all"]
        if not s.get("n"):
            continue
        print(f"  {i}. {r['model']:26s} [{r['mode']:10s}] "
              f"dist={s['dist_median']:6.1f}px  X={s['x']['med_abs']:6.1f}  "
              f"Y={s['y']['med_abs']:6.1f}  hit-rate={s['hit_rate']:.2f}")

    if args.out:
        with open(args.out, "w") as f:
            json.dump({"label": label, "results": results}, f, indent=2)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
