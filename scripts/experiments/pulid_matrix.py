#!/usr/bin/env python3
"""PuLID likeness matrix: dtype x weight x start_at x node variant, one reference.

generate_identity is gated off because PuLID on FLUX.1-dev fp8 lost the
reference's grey beard and age at weights 1.0 and 1.5. This renders the
untested combinations through the product path (ComfyUIImageGenerator, never
ComfyUI directly), names each output by its parameters, and writes a
matrix.json index next to the images for the person judging likeness by eye.

    python scripts/experiments/pulid_matrix.py --image ref.jpg \\
        --prompt "a 1940s detective in the rain" --dry-run
    python scripts/experiments/pulid_matrix.py --image ref.jpg \\
        --prompt "..." --dtypes fp8_e4m3fn,bf16 --weights 1.0,1.5 \\
        --start-ats 0.0,0.2 --only bf16

--dry-run prints every graph and submits nothing. --only keeps the cases whose
name contains every given token. An unsupported variant (pulid_classic) is
recorded in the index with the reason, not rendered. Requires a running
ComfyUI with the pulid-flux and flux-dev packs installed for real renders.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import time
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEFAULT_DTYPES = "fp8_e4m3fn,bf16"
DEFAULT_WEIGHTS = "1.0,1.5"
DEFAULT_START_ATS = "0.0,0.2"
DEFAULT_VARIANTS = "pulid_flux"



_COOLDOWN_RE = re.compile(r"try again in ([0-9.]+)s")


def _render_with_cooldown_retry(gen, name, *, attempts=6, **kwargs):
    """Back-to-back cells hit the job gate's GPU cooldown; wait it out instead of failing the cell."""
    for attempt in range(1, attempts + 1):
        try:
            return gen.generate_with_identity(**kwargs)
        except Exception as exc:  # the gate raises a plain Exception carrying the wait
            m = _COOLDOWN_RE.search(str(exc))
            if not m or attempt == attempts:
                raise
            wait = float(m.group(1)) + 2.0
            print(f"== {name}: GPU cooling down, waiting {wait:.0f}s (attempt {attempt}/{attempts})", flush=True)
            time.sleep(wait)


def _floats(text: str) -> list[float]:
    return [float(x) for x in text.split(",") if x.strip()]


def _strings(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def case_name(variant: str, dtype: str, weight: float, start_at: float) -> str:
    return f"{variant}_{dtype}_w{weight:g}_s{start_at:g}"


def build_cases(dtypes, weights, start_ats, variants, only=None) -> list[dict]:
    """Every combination, in variant/dtype/weight/start_at order, filtered by --only."""
    cases = []
    for variant, dtype, weight, start_at in itertools.product(variants, dtypes, weights, start_ats):
        name = case_name(variant, dtype, weight, start_at)
        if only and not all(tok in name for tok in only):
            continue
        cases.append({
            "name": name, "node_variant": variant, "unet_dtype": dtype,
            "weight": weight, "start_at": start_at,
        })
    return cases


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True, help="face reference image")
    ap.add_argument("--prompt", required=True, help="the new scene, in prose")
    ap.add_argument("--out", default=None,
                    help="output directory (default: data/outputs/experiments/pulid_matrix/<timestamp>)")
    ap.add_argument("--dtypes", default=DEFAULT_DTYPES, help=f"comma list (default {DEFAULT_DTYPES})")
    ap.add_argument("--weights", default=DEFAULT_WEIGHTS, help=f"comma list (default {DEFAULT_WEIGHTS})")
    ap.add_argument("--start-ats", default=DEFAULT_START_ATS, help=f"comma list (default {DEFAULT_START_ATS})")
    ap.add_argument("--end-at", type=float, default=1.0)
    ap.add_argument("--variants", default=DEFAULT_VARIANTS, help=f"comma list (default {DEFAULT_VARIANTS})")
    ap.add_argument("--only", action="append", default=[],
                    help="keep cases whose name contains this token (repeatable, all must match)")
    ap.add_argument("--dry-run", action="store_true", help="print the graphs, submit nothing")
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--width", type=int, default=768)
    ap.add_argument("--height", type=int, default=1024)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator

    image = os.path.abspath(args.image)
    if not os.path.isfile(image):
        print(f"reference image not found: {image}", file=sys.stderr)
        return 2
    out_dir = args.out or os.path.join(
        ROOT, "data", "outputs", "experiments", "pulid_matrix",
        datetime.now().strftime("%Y%m%d_%H%M%S"),
    )
    cases = build_cases(
        _strings(args.dtypes), _floats(args.weights), _floats(args.start_ats),
        _strings(args.variants), only=args.only,
    )
    gen = ComfyUIImageGenerator()
    common = dict(
        prompt=args.prompt, width=args.width, height=args.height,
        steps=args.steps, seed=args.seed, end_at=args.end_at,
    )
    index = {
        "reference": image,
        "prompt": args.prompt,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": bool(args.dry_run),
        "steps": args.steps, "seed": args.seed,
        "width": args.width, "height": args.height, "end_at": args.end_at,
        "cases": [],
    }
    os.makedirs(out_dir, exist_ok=True)
    for case in cases:
        output_path = os.path.join(out_dir, case["name"] + ".png")
        entry = {**case, "end_at": args.end_at, "output": output_path}
        overrides = dict(
            weight=case["weight"], start_at=case["start_at"],
            unet_dtype=case["unet_dtype"], node_variant=case["node_variant"],
        )
        try:
            if args.dry_run:
                graph = gen._build_pulid_workflow(
                    src_image_name=os.path.basename(image), **common, **overrides,
                )
                print(f"== {case['name']}")
                print(json.dumps(graph, indent=2))
                entry["status"] = "dry-run"
            else:
                print(f"== {case['name']}: rendering", flush=True)
                _render_with_cooldown_retry(
                    gen, case["name"], image_path=image, output_path=output_path, **common, **overrides,
                )
                entry["status"] = "ok"
        except ValueError as exc:
            # An unsupported switch (pulid_classic on FLUX, an unknown dtype):
            # recorded with the reason so the index says why the cell is empty.
            entry["status"] = "unsupported"
            entry["error"] = str(exc)
            print(f"== {case['name']}: unsupported: {exc}")
        except Exception as exc:
            entry["status"] = "failed"
            entry["error"] = str(exc)
            print(f"== {case['name']}: failed: {exc}")
        index["cases"].append(entry)

    index_path = os.path.join(out_dir, "matrix.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)
    counts = {}
    for entry in index["cases"]:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "0 cases"
    print(f"{len(cases)} cases: {summary} -> {index_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
