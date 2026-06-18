"""
Regression test for the zero-placebo guard in OfflineVideoGenerator.

Background (GitHub issue #36): on a fresh install with no real video backend
(ComfyUI not installed, diffusers/CogVideoX absent), text-to-video used to
*silently* emit a solid-color placeholder clip and report success=True. The
user saw a "blank video" the system swore had worked. The guard makes that path
fail loudly unless a caller explicitly opts into placeholders.

These tests exercise the guard's NEGATIVE case (the WORKFLOW "zero placebo" rule:
every guard must exercise its negative case).
"""

import tempfile
from pathlib import Path

import pytest

from backend.services.offline_video_generator import OfflineVideoGenerator
from backend.services.comfyui_video_generator import VideoGenerationRequest


def _no_ai_generator():
    """Build a generator and force the 'no real AI model available' condition."""
    gen = OfflineVideoGenerator()
    gen.ai_available = False
    gen.cogvideox_available = False
    gen.svd_available = False
    return gen


def test_no_ai_model_fails_loudly_instead_of_blank_video():
    """With no AI backend and no opt-in, generation must FAIL — not emit a clip."""
    gen = _no_ai_generator()
    with tempfile.TemporaryDirectory() as tmp:
        req = VideoGenerationRequest(
            prompt="a cat surfing",
            model="cogvideox-5b",
            duration_frames=4,
            width=64,
            height=64,
            output_dir=Path(tmp),
        )
        result = gen.generate_video(req)

    assert result.success is False, "must not report success when no model produced frames"
    assert result.error, "must surface an actionable error"
    assert "Manage Models" in result.error or "ComfyUI" in result.error
    assert not result.video_path, "must not produce a placeholder video file"

    # And no .mp4 should have been written anywhere under the batch dir.
    assert not list(Path(tmp).rglob("*.mp4")), "no video file should exist on the failure path"


def test_placeholder_still_available_on_explicit_optin():
    """The placeholder path is preserved, but ONLY behind an explicit opt-in."""
    pytest.importorskip("PIL")  # placeholder frames are drawn with Pillow
    gen = _no_ai_generator()
    with tempfile.TemporaryDirectory() as tmp:
        req = VideoGenerationRequest(
            prompt="a cat surfing",
            model="cogvideox-5b",
            duration_frames=2,
            width=64,
            height=64,
            output_dir=Path(tmp),
            metadata={"allow_placeholder": True},
        )
        result = gen.generate_video(req)

    # With the opt-in, the guard does NOT short-circuit with the "no frames" error.
    # (Whether muxing fully succeeds depends on imageio availability in the env;
    # the point of this test is that the explicit opt-in bypasses the hard refusal.)
    if not result.success:
        assert "Refusing to emit a blank placeholder" not in (result.error or "")
