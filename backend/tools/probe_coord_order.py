#!/usr/bin/env python3
"""Measure how a vision model points: which dialect it answers, which axis order.

Whether a model obeys a request for ``box_2d: [y1,x1,y2,x2]`` normalised to 1000
is not knowable from its name, its family, or anything Ollama reports. Nor is
whether it will answer that request at all: ministral-3:14b replies "[]" to it
and answers a plain point request. So the probe asks in every dialect the
system knows (family default first), in every axis order that dialect allows,
against targets whose positions are known, and keeps what works.

Guessing is worse than it sounds. A transposed answer does not fail loudly: it
lands a plausible click on the wrong thing, mirrored across the diagonal, and
reports success. The discriminator is brutally simple on a board with targets
off the diagonal: the wrong order costs hundreds of pixels, the right one tens.

Records into data/training/servo_calibration.json (the one measurement store):
the `coords` section (winning style/order, and every dialect tried) and, from
the winning run, the `accuracy` section. Failures are recorded too — "this
model returns nothing usable in any dialect" is expensive to learn and is
exactly what the resolver needs to refuse a click.

Usage:
    GUAARDVARK_MODE=test backend/venv/bin/python -m backend.tools.probe_coord_order \
        --models ministral-3:14b --frames <bench>/manifest.json
    ... --migrate-legacy      # move the old model_coord_probe.json into the store
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from datetime import datetime
from pathlib import Path
from typing import Optional

LEGACY_STORE = Path(__file__).resolve().parents[2] / "data" / "training" / "model_coord_probe.json"
BENCH_ROOT = Path(__file__).resolve().parents[2] / "data" / "training" / "eye_bench"
# A two-order dialect must separate its orders by this factor before we believe
# it; a transposition on a 1000px board costs hundreds of pixels, so a genuinely
# ambiguous result stays ambiguous rather than being resolved by noise.
MIN_RATIO = 1.6
# Single-order dialects have no transposition to guard against. They pass on a
# usability bound instead: a pointer that is consistently ~140px off has a
# known convention with poor accuracy, and exposing that is the accuracy
# section's job, not the coords section's.
USABLE_FRACTION = 0.25


def _trim(frames, max_targets):
    out, n = [], 0
    for fr in frames:
        keep = []
        for t in fr["targets"]:
            if n >= max_targets:
                break
            keep.append(t); n += 1
        if keep:
            out.append({"image": fr["image"], "targets": keep})
        if n >= max_targets:
            break
    return out


def _run(model, size, frames, style, order, min_num_predict):
    from backend.tools.eye_bakeoff import eval_frames, MODE_OVERLAYS
    from backend.services.model_capability_data import style_overlay
    overlay = dict(MODE_OVERLAYS["anchor"])
    overlay.update(style_overlay(style, order))
    overlay["min_num_predict"] = min_num_predict
    r = eval_frames(model, size, frames, overlay)
    rows = [x for x in r["targets"] if x.get("err_x") is not None]
    return {
        "style": style, "order": order, "n": len(rows),
        "median_dist": round(st.median([x["dist"] for x in rows]), 1) if rows else None,
        "median_x": round(st.median([abs(x["err_x"]) for x in rows]), 1) if rows else None,
        "median_y": round(st.median([abs(x["err_y"]) for x in rows]), 1) if rows else None,
        "hit_rate": r["score"]["all"].get("hit_rate") if rows else None,
    }


def probe(model: str, manifest: str, max_targets: int = 12,
          styles: Optional[list] = None) -> dict:
    from backend.tools.eye_bakeoff import load_manifest
    from backend.services.model_capability_data import COORD_STYLES
    from backend.services.model_capability_resolver import coords_for

    size, frames = load_manifest(manifest)
    frames = _trim(frames, max_targets)
    total = sum(len(f["targets"]) for f in frames)
    need = max(4, total // 2)
    bound = USABLE_FRACTION * max(size)

    conv = coords_for(model, tuple(size))
    fam_style = conv.style
    budget = int(getattr(conv, "min_num_predict", 128) or 128)
    # A recorded failure carries no budget, and it must not hide the family's
    # hint: a thinking model probed at 128 tokens answers nothing in any
    # dialect and then looks unusable forever. Take the larger of the two.
    try:
        from backend.utils.ollama_resource_manager import get_model_info
        from backend.services.model_capability_data import FAMILY_COORD_DEFAULTS
        fam = ((get_model_info(model) or {}).get("architecture") or "").lower()
        fd = FAMILY_COORD_DEFAULTS.get(fam) or {}
        budget = max(budget, int(fd.get("min_num_predict", 0) or 0))
        if conv.source == "probe_failed" and fd.get("style"):
            fam_style = fd["style"]
    except Exception:
        pass
    order_of_styles = styles or ([fam_style] + [s for s in COORD_STYLES if s != fam_style])

    tried = []
    verdicts = []   # (median_dist, style, order, record)
    for style in order_of_styles:
        spec = COORD_STYLES[style]
        results = {}
        for order in spec["orders"]:
            rec = _run(model, size, frames, style, order, budget)
            tried.append(rec)
            results[order] = rec
            print(f"  {model:40s} {style:16s} order={order}  n={rec['n']:3d}  "
                  f"dist={rec['median_dist']}  |X|={rec['median_x']}  |Y|={rec['median_y']}")
        ok = {o: r for o, r in results.items() if r["n"] >= need and r["median_dist"] is not None}
        if not ok:
            continue
        if len(spec["orders"]) > 1:
            if len(ok) == 1:
                o, r = next(iter(ok.items()))
                verdicts.append((r["median_dist"], style, o, r))
            else:
                (o1, r1), (o2, r2) = sorted(ok.items(), key=lambda kv: kv[1]["median_dist"])[:2]
                if r1["median_dist"] <= r2["median_dist"] / MIN_RATIO:
                    verdicts.append((r1["median_dist"], style, o1, r1))
                else:
                    print(f"    {style}: inconclusive ({o1} {r1['median_dist']}px vs {o2} {r2['median_dist']}px)")
        else:
            o, r = next(iter(ok.items()))
            if r["median_dist"] <= bound:
                verdicts.append((r["median_dist"], style, o, r))
            else:
                print(f"    {style}: answers, but {r['median_dist']}px is past the {bound:.0f}px usability bound")

    stamp = datetime.now().isoformat(timespec="seconds")
    board = Path(manifest).parent.name
    if not verdicts:
        print(f"  -> {model}: UNUSABLE for pointing in {len(order_of_styles)} dialect(s)")
        return {"coords": {"style": None, "order": None, "grid": None, "confidence": 0.0,
                           "reason": "no dialect produced a usable answer",
                           "tried": tried, "probed_at": stamp, "board": board},
                "accuracy": None, "size": size}
    verdicts.sort(key=lambda v: v[0])
    best_d, best_style, best_order, best = verdicts[0]
    # A tie within the margin keeps the family default: both are right, prefer
    # the dialect already declared for the family.
    for d, sty, o, r in verdicts:
        if sty == fam_style and d <= best_d * MIN_RATIO:
            best_d, best_style, best_order, best = d, sty, o, r
            break
    grid = COORD_STYLES[best_style]["grid"]
    print(f"  -> {model}: {best_style} / {best_order}  ({best_d}px)")
    return {
        "coords": {"style": best_style, "order": best_order, "grid": grid,
                   "normalised": bool(grid), "confidence": 0.9,
                   "min_num_predict": budget,
                   "reason": f"{best_style}/{best_order} {best_d}px", "tried": tried,
                   "probed_at": stamp, "board": board},
        "accuracy": {"median_px": best["median_dist"], "median_x": best["median_x"],
                     "median_y": best["median_y"], "hit_rate": best["hit_rate"],
                     "n": best["n"], "mode": "anchor", "style": best_style,
                     "board": board, "measured_at": stamp},
        "size": size,
    }


def migrate_legacy() -> int:
    from backend.services.servo_knowledge_store import record_measurement
    try:
        legacy = json.loads(LEGACY_STORE.read_text())
    except Exception:
        print(f"no legacy file at {LEGACY_STORE}")
        return 0
    moved = 0
    for tag, rec in legacy.items():
        board = rec.get("board")
        man = BENCH_ROOT / (board or "") / "manifest.json"
        if not board or not man.exists():
            print(f"  skip {tag}: board manifest missing ({man}); not guessing a resolution")
            continue
        w, h = json.loads(man.read_text()).get("display") or (None, None)
        if not w:
            print(f"  skip {tag}: manifest has no display size")
            continue
        measured = rec.get("measured") or {}
        tried = [{"style": "google_box2d", "order": o, **{k: v for k, v in m.items()}}
                 for o, m in measured.items()]
        coords = {"style": "google_box2d" if rec.get("order") else None,
                  "order": rec.get("order"), "grid": rec.get("grid", 1000),
                  "normalised": bool(rec.get("order")), "confidence": rec.get("confidence", 0.9),
                  "reason": rec.get("reason", ""), "tried": tried,
                  "probed_at": rec.get("probed_at", ""), "board": board, "source": "legacy_probe"}
        record_measurement(tag, int(w), int(h), "coords", coords)
        print(f"  moved {tag} -> {tag}@{w}x{h}")
        moved += 1
    print(f"{moved} entr{'y' if moved == 1 else 'ies'} migrated; {LEGACY_STORE.name} left in place for one release")
    return 0


def main(argv: Optional[list] = None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="")
    ap.add_argument("--frames", default="")
    ap.add_argument("--max-targets", type=int, default=12)
    ap.add_argument("--styles", default="", help="comma-separated subset of COORD_STYLES")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--migrate-legacy", action="store_true")
    args = ap.parse_args(argv)

    if args.migrate_legacy:
        return migrate_legacy()
    if not args.models or not args.frames:
        ap.error("--models and --frames are required unless --migrate-legacy")

    from backend.services.servo_knowledge_store import record_measurement
    styles = [s.strip() for s in args.styles.split(",") if s.strip()] or None
    for m in [x.strip() for x in args.models.split(",") if x.strip()]:
        res = probe(m, args.frames, args.max_targets, styles)
        if args.dry_run:
            continue
        w, h = res["size"]
        record_measurement(m, int(w), int(h), "coords", res["coords"])
        if res["accuracy"]:
            record_measurement(m, int(w), int(h), "accuracy", res["accuracy"])
        print(f"  recorded {m}@{w}x{h}")
    if args.dry_run:
        print("\ndry-run: nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
