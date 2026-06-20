"""
Zero-placebo guard for the ComfyUI/Wan video path (issue #36 Phase 3).

ComfyUI can emit a black/empty clip when a model/loader fails silently, and the
old code reported that as success. `_looks_like_blank_video` must reject those.
These tests generate real fixtures with ffmpeg (no GPU needed) and assert:
  - an empty/stub file is rejected (size gate),
  - a fully-black clip is rejected (compressed -> size gate; uncompressed -> blackdetect),
  - a real, non-black clip passes.
"""

import shutil
import subprocess

import pytest

from backend.services.comfyui_video_generator import _looks_like_blank_video

ffmpeg = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(ffmpeg is None, reason="ffmpeg not installed")


def _run(args):
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", *args],
                   check=True, capture_output=True)


def test_empty_file_rejected(tmp_path):
    f = tmp_path / "empty.mp4"
    f.write_bytes(b"")
    assert _looks_like_blank_video(f) is not None


def test_missing_file_rejected(tmp_path):
    assert _looks_like_blank_video(tmp_path / "nope.mp4") is not None


def test_compressed_black_rejected_by_size(tmp_path):
    # A real all-black render compresses to a few KB -> caught by the size gate.
    f = tmp_path / "black.mp4"
    _run(["-f", "lavfi", "-i", "color=c=black:size=320x240:duration=2:rate=12",
          "-pix_fmt", "yuv420p", str(f)])
    assert _looks_like_blank_video(f) is not None


def test_large_black_rejected_by_blackdetect(tmp_path):
    # Uncompressed black clears the size gate, so blackdetect must reject it.
    f = tmp_path / "rawblack.avi"
    _run(["-f", "lavfi", "-i", "color=c=black:size=256x256:duration=2:rate=12",
          "-c:v", "rawvideo", "-pix_fmt", "yuv420p", str(f)])
    assert f.stat().st_size > 10 * 1024
    reason = _looks_like_blank_video(f)
    assert reason is not None and "black" in reason.lower()


def test_real_clip_passes(tmp_path):
    # A real, non-black clip must NOT be flagged (no false positive).
    f = tmp_path / "real.avi"
    _run(["-f", "lavfi", "-i", "testsrc=duration=2:size=256x256:rate=12",
          "-c:v", "rawvideo", "-pix_fmt", "yuv420p", str(f)])
    assert _looks_like_blank_video(f) is None
