#!/usr/bin/env python3
"""Convert still images to movie clips using ffmpeg (no GPU / no AI model).

Unlike Image-to-Video, this moves the CAMERA only (or holds the frame still) —
the artwork is never re-rendered, so there is zero distortion and zero color
change. Three motion patterns:
  - static           : a still frame held for the duration (pixel-perfect)
  - ken_burns_zoom   : slow camera push-in (zoom)
  - ken_burns_pan    : slow left-to-right camera pan

Generated clips are written to --out (default data/uploads/Videos/FFmpeg/<batch>)
and each is registered as a Document so it appears in the Media Library / Files.

Examples:
  # Zoom on every image, 5s, 720p
  python3 scripts/ffmpeg_stills.py data/clients/followup/kwiksher/video/images/*.png \
      --pattern ken_burns_zoom --duration 5 --width 1280 --height 720

  # Generate all three patterns for one image
  python3 scripts/ffmpeg_stills.py logo.png --all-patterns

  # Dry run (just print what would happen)
  python3 scripts/ffmpeg_stills.py images/ --dry-run
"""

import argparse
import glob
import os
import sys
from pathlib import Path

# Make the backend importable when run from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services import ffmpeg_still_video_generator as ffmpeg_gen  # noqa: E402


def _run_with_app(func):
    """Registering generated files touches the DB (needs a Flask app context)."""
    from backend.app import get_or_create_app
    app = get_or_create_app()
    with app.app_context():
        return func()


def expand_paths(inputs):
    """Expand file args / directories / globs into a flat list of image paths."""
    paths = []
    for arg in inputs:
        for p in glob.glob(arg):
            path = Path(p)
            if path.is_dir():
                paths += [str(x) for x in sorted(path.iterdir()) if x.suffix.lower() in (
                    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"
                )]
            elif path.is_file():
                paths.append(str(path))
    return paths


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="Image files, dirs, or globs")
    ap.add_argument("--pattern", default="ken_burns_zoom", choices=list(ffmpeg_gen.PATTERNS),
                    help="Motion pattern (default: ken_burns_zoom)")
    ap.add_argument("--all-patterns", action="store_true",
                    help="Generate all three patterns for each image")
    ap.add_argument("--duration", type=float, default=5.0, help="Clip duration in seconds")
    ap.add_argument("--fps", type=int, default=25, help="Frames per second")
    ap.add_argument("--width", type=int, default=1280, help="Output width")
    ap.add_argument("--height", type=int, default=720, help="Output height")
    ap.add_argument("--out", default=None, help="Output directory (default: uploads/Videos/FFmpeg/<batch>)")
    ap.add_argument("--folder-name", default="Videos", help="Destination folder in the Media Library")
    ap.add_argument("--dry-run", action="store_true", help="Print what would happen, don't render")
    args = ap.parse_args()

    if not ffmpeg_gen.ffmpeg_available():
        print("Error: ffmpeg not found on PATH. Install it with `brew install ffmpeg`.", file=sys.stderr)
        sys.exit(1)

    images = expand_paths(args.inputs)
    if not images:
        print("No image files found in the given inputs.", file=sys.stderr)
        sys.exit(1)

    patterns = list(ffmpeg_gen.PATTERNS) if args.all_patterns else [args.pattern]
    print(f"{len(images)} image(s), {len(patterns)} pattern(s): {', '.join(patterns)}")
    print(f"duration={args.duration}s fps={args.fps} {args.width}x{args.height}")

    total_ok = 0
    total_fail = 0

    def run_all():
        nonlocal total_ok, total_fail
        for pattern in patterns:
            if args.dry_run:
                for img in images:
                    print(f"[dry-run] {pattern}: {Path(img).name}")
                continue
            batch_label = f"all_{len(patterns)}_patterns" if args.all_patterns else patterns[0]
            sub = args.out or str(ffmpeg_gen.FFMPEG_DIR / batch_label)
            results = ffmpeg_gen.generate_still_clip_batch(
                image_paths=images,
                pattern=pattern,
                duration_s=args.duration,
                fps=args.fps,
                width=args.width,
                height=args.height,
                folder_name=args.folder_name,
                subfolder_name=sub if args.out else None,
            )
            for r in results:
                if r["success"]:
                    total_ok += 1
                    print(f"  OK   {r['filename']}  (doc {r['document_id']})")
                else:
                    total_fail += 1
                    print(f"  FAIL {Path(r['source']).name}: {r['error']}", file=sys.stderr)

    if args.dry_run:
        run_all()  # dry-run: only prints, never touches the DB
        print("Dry run complete \u2014 nothing was rendered.")
        return
    _run_with_app(run_all)
    print(f"\nDone. Created: {total_ok}, Failed: {total_fail}.")
    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
