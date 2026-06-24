#!/usr/bin/env python3
"""Pre-train quality gate for character LoRA datasets — refuse to launch on bad data.

WHY (2026-06-23): the first sage_harlow LoRA trained on a dataset that, in hindsight, was
guaranteed to produce the "horse-head" failures: captions were ignored (caption_strategy was
"filename", not "textfile"), there were ZERO full-body images (so no learned body), and 5/8
captions were near-identical (no caption→concept signal). NONE of that was caught before a
3–4 hour GPU run. This gate is the catch. It asserts, BEFORE training launches:

  1. the dataset has images, and EVERY image has a non-empty .txt caption sidecar;
  2. (if a SimpleTuner multidatabackend.json is given) caption_strategy == "textfile";
  3. the trigger word appears in EVERY caption (so identity actually binds to the token);
  4. pose coverage: at least N full-body / three-quarter / wide shots (a learned body);
  5. caption diversity: not a pile of near-duplicate captions.

Exit 0 = pass (safe to train), exit 1 = fail (do NOT train). Filesystem-only: no DB, no GPU,
no torch — cheap to run as the first step of training. Designed to be called by
``training/retrain_character.sh`` and by hand.

Usage
-----
  python scripts/pretrain_gate.py training/sage_harlow/dataset --trigger sage_harlow \\
      --config plugins/lora_trainer/SimpleTuner/config/multidatabackend.json
  python scripts/pretrain_gate.py <dir> --trigger <word> --min-full-body 4 --json
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def _framing_helpers():
    """Reuse the captioner's canonical framing vocab + detector (single source of truth)."""
    try:
        from backend.services.character_captioner import detect_framing, FULL_BODY_FRAMINGS
        return detect_framing, FULL_BODY_FRAMINGS
    except Exception:  # pragma: no cover — keep the gate runnable even if the import path breaks
        FULL = {"three-quarter view", "full body", "wide shot"}
        def detect_framing(c: str):
            c = (c or "").lower()
            for tag in ("wide shot", "full body", "three-quarter view", "upper body",
                        "head and shoulders", "close-up"):
                if tag in c:
                    return tag
            if re.search(r"\bfull[- ]length\b", c):
                return "full body"
            if re.search(r"\b3/4\b|\bthree quarter\b", c):
                return "three-quarter view"
            return None
        return detect_framing, FULL


def _tokens(caption: str) -> set:
    return {t.strip().lower() for t in caption.split(",") if t.strip()}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def run_gate(dataset_dir: str, trigger: str, *, config: str = "",
             min_full_body: int | None = None, dup_threshold: float = 0.9) -> dict:
    detect_framing, FULL_BODY_FRAMINGS = _framing_helpers()
    d = Path(dataset_dir)
    failures: list[str] = []
    warnings: list[str] = []

    images = sorted(p for p in d.iterdir() if p.suffix.lower() in IMAGE_EXTS) if d.is_dir() else []
    n = len(images)
    if n == 0:
        return {"pass": False, "dir": str(d), "images": 0,
                "failures": [f"no images found in {d}"], "warnings": [], "framing": {}}

    # 1 + 3: sidecars present & non-empty; trigger in every caption.
    captions: dict[str, str] = {}
    trig = (trigger or "").strip().lower()
    for img in images:
        sc = img.with_suffix(".txt")
        if not sc.exists():
            failures.append(f"{img.name}: missing caption sidecar ({sc.name})")
            continue
        text = sc.read_text(encoding="utf-8").strip()
        if not text:
            failures.append(f"{img.name}: empty caption sidecar")
            continue
        captions[img.name] = text
        if trig and trig not in text.lower():
            failures.append(f"{img.name}: trigger word '{trigger}' not in caption")

    # 2: caption_strategy must be textfile (the bug that silently ignored captions last time).
    strategy = None
    if config:
        try:
            cfg = json.loads(Path(config).read_text(encoding="utf-8"))
            # multidatabackend.json is a list of backends or a single dict.
            backends = cfg if isinstance(cfg, list) else [cfg]
            for b in backends:
                if isinstance(b, dict) and "caption_strategy" in b:
                    strategy = b.get("caption_strategy")
                    if strategy != "textfile":
                        failures.append(
                            f"caption_strategy is '{strategy}' (must be 'textfile' — 'filename' "
                            f"silently ignores your .txt captions, the original horse-head bug)")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"could not read caption_strategy from {config}: {e}")

    # 4: pose coverage — need a learned body.
    framing_tally: dict[str, int] = {}
    for cap in captions.values():
        fr = detect_framing(cap) or "unknown"
        framing_tally[fr] = framing_tally.get(fr, 0) + 1
    full_body = sum(framing_tally.get(f, 0) for f in FULL_BODY_FRAMINGS)
    need_full = min_full_body if min_full_body is not None else max(3, math.ceil(0.20 * n))
    if full_body < need_full:
        failures.append(
            f"only {full_body} full-body/three-quarter/wide shot(s); need >= {need_full}. "
            f"Without below-the-shoulders supervision the LoRA has no body and improvises one "
            f"under motion (the horse-head failure). framing={framing_tally}")

    # 5: caption diversity — guard against the "5/8 identical captions" overfit.
    cap_list = list(captions.values())
    tok = [_tokens(c) for c in cap_list]
    dup = 0
    for i in range(len(cap_list)):
        if any(_jaccard(tok[i], tok[j]) >= dup_threshold for j in range(i)):
            dup += 1
    unique = len(cap_list) - dup
    need_unique = max(1, math.ceil(0.60 * len(cap_list))) if cap_list else 0
    if cap_list and unique < need_unique:
        failures.append(
            f"caption diversity too low: only {unique}/{len(cap_list)} effectively-unique captions "
            f"(>= {dup_threshold} token overlap counts as duplicate); need >= {need_unique}. "
            f"Near-identical captions over-anchor the LoRA to one pose/outfit.")

    if n < 12:
        warnings.append(f"only {n} images — fine for a quick character LoRA, but more (20-30 with "
                        f"varied framing/outfits) materially improves robustness.")

    return {
        "pass": not failures,
        "dir": str(d),
        "images": n,
        "captioned": len(captions),
        "caption_strategy": strategy,
        "framing": framing_tally,
        "full_body_count": full_body,
        "full_body_required": need_full,
        "unique_captions": unique if cap_list else 0,
        "failures": failures,
        "warnings": warnings,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Pre-train quality gate for a character LoRA dataset.")
    ap.add_argument("dataset_dir")
    ap.add_argument("--trigger", required=True)
    ap.add_argument("--config", default="", help="SimpleTuner multidatabackend.json (checks caption_strategy).")
    ap.add_argument("--min-full-body", type=int, default=None, help="Override required full-body count.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    r = run_gate(args.dataset_dir, args.trigger, config=args.config, min_full_body=args.min_full_body)

    if args.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"\nPre-train gate: {r['dir']}")
        print(f"images={r['images']} captioned={r.get('captioned', 0)} "
              f"caption_strategy={r.get('caption_strategy')} full_body={r.get('full_body_count')}"
              f"/{r.get('full_body_required')}")
        print(f"framing={r.get('framing')}")
        for w in r["warnings"]:
            print(f"  [warn] {w}")
        for f in r["failures"]:
            print(f"  [FAIL] {f}")
        print("\nRESULT:", "PASS — safe to train." if r["pass"]
              else "FAIL — do NOT train; fix the dataset above.")
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
