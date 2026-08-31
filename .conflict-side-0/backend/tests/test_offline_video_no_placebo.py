"""
Regression test for the zero-placebo guard in OfflineVideoGenerator.

Background (GitHub issue #36): on a fresh install with no real video backend
(ComfyUI not installed, diffusers/CogVideoX absent), text-to-video used to
*silently* emit a solid-color placeholder clip and report success=True. The
user saw a "blank video" the system swore had worked. The placeholder path has
been removed entirely (NO-MOCKS charter): that path now ALWAYS fails loudly — the
legacy allow_placeholder opt-in is ignored.

These tests exercise the guard's NEGATIVE case (the WORKFLOW "zero placebo" rule:
every guard must exercise its negative case).
"""

import tempfile
from pathlib import Path

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


def test_placeholder_optin_is_ignored_and_still_refuses():
    """The placeholder path is GONE (NO-MOCKS charter): even with the legacy
    allow_placeholder opt-in, generation must STILL fail and produce no clip."""
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

    assert result.success is False, "opt-in must NOT resurrect the placeholder path"
    assert not result.video_path, "must not produce a placeholder video"
    assert not list(Path(tmp).rglob("*.mp4")), "no fake clip should be written even with the flag"
