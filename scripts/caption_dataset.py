#!/usr/bin/env python3
"""Auto-caption a character LoRA training dataset (offline VLM).

Writes ``<stem>.txt`` sidecars next to each image, with the trigger word front-loaded and the
fixed identity marks appended — so the LoRA binds freckles / eye color / marks to the token and
learns varied framing. Reuses the offline Gemma-vision path (Ollama). NO training, NO GPU diffusion.

Examples
--------
  # Caption the sage_harlow set, pulling identity marks from its profile, review first:
  python scripts/caption_dataset.py training/sage_harlow/dataset \\
      --trigger sage_harlow --profile training/sage_harlow/character_profile.md --dry-run

  # Then actually write the sidecars (only the missing ones):
  python scripts/caption_dataset.py training/sage_harlow/dataset \\
      --trigger sage_harlow --profile training/sage_harlow/character_profile.md

  # Explicit marks instead of a profile, overwriting existing captions:
  python scripts/caption_dataset.py path/to/dataset --trigger my_char \\
      --marks "pale freckled skin, hazel-green eyes, beauty mark on left cheek" --overwrite

Requires Ollama running with a vision model (same one the app uses). If the VLM is unavailable,
each caption degrades to "<trigger>, <marks>" so you still get a valid (if sparse) sidecar.
"""
import argparse
import json
import sys
from pathlib import Path

# Make the repo root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto-caption a character LoRA dataset (offline VLM).")
    ap.add_argument("dataset_dir", help="Directory of training images.")
    ap.add_argument("--trigger", required=True, help="LoRA trigger word (e.g. sage_harlow).")
    ap.add_argument("--marks", default="", help="Fixed identity marks to append to every caption.")
    ap.add_argument("--profile", default="", help="character_profile.md to auto-extract marks from "
                                                  "(used only if --marks not given).")
    ap.add_argument("--overwrite", action="store_true", help="Re-caption images that already have a .txt.")
    ap.add_argument("--dry-run", action="store_true", help="Caption + print but do not write sidecars.")
    ap.add_argument("--json", action="store_true", help="Emit the full result summary as JSON.")
    args = ap.parse_args()

    from backend.services.character_captioner import caption_dataset, marks_from_profile, FULL_BODY_FRAMINGS

    marks = args.marks.strip()
    if not marks and args.profile:
        marks = marks_from_profile(args.profile)
        if marks:
            print(f"[i] identity marks from profile: {marks}")

    summary = caption_dataset(
        args.dataset_dir,
        trigger=args.trigger,
        identity_marks=marks,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"\nDataset: {summary['dir']}")
    print(f"images={summary['images']}  written={summary['written']}  skipped={summary['skipped']}")
    print(f"framing coverage: {summary['framing_tally']}  (full-body={summary['full_body_count']})")
    for r in summary["results"]:
        print(f"  [{r['action']:7}] {r['image']:16} ({r['framing'] or '?'}): {r['caption']}")
    if summary["full_body_count"] == 0:
        print("\n[!] WARNING: zero full-body / three-quarter / wide shots detected. The LoRA will "
              "have no learned body — add full-body images (this is the horse-head root cause).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
