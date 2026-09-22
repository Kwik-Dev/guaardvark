#!/usr/bin/env python3
"""Capture a clean, truth-labelled frame set from the grid trainer.

The one rule this tool exists to enforce: **it never clicks anything.** A frame
set is only worth what its ground truth is worth, and the fastest way to ruin
ground truth is to let the board react to the measurement. So this drives the
page's own layout function, reads the target positions out of the DOM, takes a
picture, and moves on. Nothing is ever clicked, so nothing is ever marked, so
every target in every frame is clean by construction rather than by inspection.

Truth comes from ``window.__truth()`` — the page's own ``getBoundingClientRect``
plus ``mozInnerScreenX/Y`` — not from the pixels. Reading it out of the DOM is
exact and stays exact whatever the page later looks like.

Four rounds of five dots produces 20 distinct positions. That number is not
arbitrary: ``servo_calibrate.fit_and_gate`` refuses to fit fewer than 12 after
de-duplication, and a fixed board can never reach it.

Prereqs: agent display up, Firefox with BiDi on :9222 (GUAARDVARK_AGENT_CDP=1).

Usage:
    GUAARDVARK_MODE=test backend/venv/bin/python -m backend.tools.eye_capture \
        --rounds 4 --dots 5
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

TRAINER = Path(__file__).resolve().parents[2] / "data" / "agent" / "files" / "vision_trainer_grid.html"
OUT_ROOT = Path(__file__).resolve().parents[2] / "data" / "training" / "eye_bench"


def capture_set(rounds: int = 4, dots: int = 5, settle_s: float = 0.6,
                navigate: bool = True, out_dir: Optional[Path] = None,
                seed_base: int = 0) -> Path:
    from backend.tools.servo_calibrate import _bidi
    from backend.services.local_screen_backend import LocalScreenBackend

    if navigate:
        from backend.services.social_outreach.reddit_outreach import _bidi_navigate
        if not _bidi_navigate(TRAINER.as_uri()):
            raise SystemExit(f"could not navigate to {TRAINER}")
        time.sleep(2.0)

    if _bidi("typeof window.__truth") != "function":
        raise SystemExit(
            "page does not expose __truth() — is the grid trainer actually open?")

    out = Path(out_dir) if out_dir else OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    screen = LocalScreenBackend()
    frames, seen = [], set()
    for rnd in range(1, rounds + 1):
        # seed_base matters more than it looks. The page's layout is seeded so a
        # round can be re-staged exactly, which means two captures taken with the
        # same seeds put the dots in the same places — and servo_calibrate's
        # dedupe_by_position then throws the second set away as duplicates. Give
        # each capture its own base to actually add positions rather than repeats.
        seed = seed_base + rnd
        _bidi(f"window.__newRound({int(dots)}, {seed})")
        # Belt and braces: the page clears on a new round anyway, but a stray
        # marker left by a human poking at the board would otherwise ride along
        # into a frame set whose whole claim is that it has none.
        _bidi("window.__clearMarkers()")
        time.sleep(settle_s)

        targets = json.loads(_bidi("window.__truth()"))
        if not targets:
            raise SystemExit(f"round {rnd}: __truth() returned no targets")

        img, _ = screen.capture()
        name = f"frame_{rnd:03d}.webp"
        img.save(out / name, "WEBP", quality=92)

        frames.append({"image": name, "targets": [
            {"prompt": t["prompt"], "colour": t.get("letter", ""),
             "cx": t["cx"], "cy": t["cy"], "r": t.get("r", 20), "clean": True}
            for t in targets]})
        for t in targets:
            seen.add((t["cx"], t["cy"]))
        print(f"  round {rnd} (seed {seed}): {len(targets)} targets -> {name}")

    display = list(screen.screen_size())
    manifest = {
        "display": display,
        "source": (f"eye_capture {datetime.now().isoformat(timespec='seconds')} "
                   f"rounds={rounds} dots={dots} seed_base={seed_base} "
                   f"page={TRAINER.name} (no clicks)"),
        "distinct_positions": len(seen),
        "frames": frames,
    }
    path = out / "manifest.json"
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nwrote {path}")
    print(f"  {len(frames)} frames, {sum(len(f['targets']) for f in frames)} targets, "
          f"{len(seen)} distinct positions, display {display[0]}x{display[1]}")
    if len(seen) < 12:
        print(f"  WARNING: servo_calibrate.fit_and_gate needs 12 distinct positions "
              f"after de-duplication and this set has {len(seen)}. Raise --rounds.")
    return path


def main(argv: Optional[list] = None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--dots", type=int, default=5)
    ap.add_argument("--settle", type=float, default=0.6)
    ap.add_argument("--no-navigate", action="store_true")
    ap.add_argument("--seed-base", type=int, default=0,
                    help="offset the per-round layout seeds; use a different base for "
                         "each capture or the positions repeat and get de-duplicated away")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    capture_set(rounds=args.rounds, dots=args.dots, settle_s=args.settle,
                navigate=not args.no_navigate,
                out_dir=Path(args.out) if args.out else None,
                seed_base=args.seed_base)


if __name__ == "__main__":
    main()
