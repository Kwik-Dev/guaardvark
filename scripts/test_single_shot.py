#!/usr/bin/env python3
"""Single-shot render test: verify the Wan I2V clip-saving path works end-to-end.

Renders ONE short clip from a real storyboard frame via Wan22I2VGenerator and
verifies the MP4 is written to the requested output path. This exercises the
exact code path the Film Crew render uses to save each shot's clip, without
running the full 7-shot production.
"""
import os
import sys
import tempfile
from pathlib import Path

# Ensure the repo root is importable (backend package lives there).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(".env")

from backend.services.comfyui_video_generator import Wan22I2VGenerator

IMAGE = "/Users/ymmtny/GitHub/guaardvark/data/outputs/storyboards/3/shot_1_1.png"
PROMPT = "WIDE: The forest swallows the path. Elara walks forward, leaves rustling."


def main():
    if not Path(IMAGE).exists():
        print(f"FAIL: storyboard image missing: {IMAGE}")
        return 1

    out_dir = Path(tempfile.mkdtemp(prefix="shot_test_"))
    clip_path = str(out_dir / "shot_1_1.mp4")

    print(f"Rendering single shot -> {clip_path}")
    print(f"  image: {IMAGE}")
    print(f"  prompt: {PROMPT[:50]}...")

    gen = Wan22I2VGenerator(fps=24)
    result = gen.i2v_from_image(
        image_path=IMAGE,
        prompt=PROMPT,
        loras=[],
        duration_seconds=1.0,   # short clip for a quick test
        output_path=clip_path,
    )

    if not os.path.exists(clip_path):
        print(f"FAIL: clip was NOT saved to {clip_path}")
        return 1

    size = os.path.getsize(clip_path)
    print(f"PASS: clip saved ({size} bytes)")
    print(f"  path: {clip_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
