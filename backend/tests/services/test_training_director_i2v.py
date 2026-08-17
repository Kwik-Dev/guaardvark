#!/usr/bin/env python3
"""Contract between the training director's I2V call and the batch-video API.

`visuals.animate` falls back to the still on any dispatch failure, by design —
motion must never block a production. That silence hides a malformed request
indefinitely, so the request shape is asserted here rather than discovered in a
render.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
os.environ["GUAARDVARK_MODE"] = "test"

# The director's modules import their siblings by bare name.
sys.path.insert(0, str(REPO / "scripts" / "training_director"))

import visuals  # noqa: E402
from config import VIDEO_FPS, VIDEO_FRAMES, VIDEO_MODEL  # noqa: E402

from backend.services.video_model_registry import (  # noqa: E402
    VIDEO_MODEL_REGISTRY,
)


class TestI2VPayloadShape(unittest.TestCase):
    """Mirrors backend/api/batch_video_generation_api.py:generate_image_to_video_batch."""

    def setUp(self):
        self.payload = visuals.i2v_payload("/tmp/s00_0.png", "a slate roof at dawn")

    def test_sends_image_paths_as_a_list(self):
        # The endpoint 400s on anything else: it reads data["image_paths"].
        self.assertIsInstance(self.payload["image_paths"], list)
        self.assertEqual(self.payload["image_paths"], ["/tmp/s00_0.png"])

    def test_sends_frame_count_and_fps_not_seconds(self):
        self.assertEqual(self.payload["duration_frames"], VIDEO_FRAMES)
        self.assertEqual(self.payload["fps"], VIDEO_FPS)
        self.assertNotIn("duration", self.payload)

    def test_carries_the_series_style_and_negative_prompt(self):
        self.assertIn(visuals.STYLE_SUFFIX, self.payload["prompt"])
        self.assertEqual(self.payload["negative_prompt"], visuals.NEGATIVE)

    def test_prompt_enhancement_is_off(self):
        # The enhancer reintroduces the countable detail the prompt discipline
        # in visuals.py deliberately excludes.
        self.assertIs(self.payload["enhance_prompt"], False)

    def test_requests_the_project_frame_size(self):
        self.assertGreater(self.payload["width"], 0)
        self.assertGreater(self.payload["height"], 0)


class TestConfiguredModel(unittest.TestCase):

    def test_default_model_exists_in_the_registry(self):
        # preflight_video_model rejects an unknown id, so a plausible-looking
        # name that is not a registry key disables motion silently.
        self.assertIn(VIDEO_MODEL, VIDEO_MODEL_REGISTRY)

    def test_default_model_has_an_i2v_branch_in_the_dispatcher(self):
        # comfyui_video_generator routes I2V by explicit per-model branches, so a
        # T2V-only id would dispatch and then render without the start frame.
        self.assertIn(VIDEO_MODEL, {
            "wan22-14b-i2v", "wan22-5b", "cogvideox-5b-i2v",
            "ltx23-distilled-fp8", "ltx25-distilled-int8",
        })

    def test_payload_dimensions_suit_the_model_budget(self):
        entry = VIDEO_MODEL_REGISTRY[VIDEO_MODEL]
        # Oversize requests are clamped and aligned server-side; assert the
        # metadata the clamp depends on is actually declared for this model.
        self.assertGreater(entry["max_pixel_area"], 0)
        self.assertEqual(entry["dimension_alignment"] % 8, 0)


class TestAnimateNeverBlocksAProduction(unittest.TestCase):
    """A failed I2V render must degrade to the still, never raise."""

    def setUp(self):
        self.still = Path("/tmp/s01_0.png")
        self.dest = Path("/tmp/s01_motion.mp4")

    def test_a_hard_failure_returns_the_still(self):
        with mock.patch.object(visuals, "release_voice_vram"), \
             mock.patch.object(visuals, "_dispatch_i2v",
                               side_effect=RuntimeError("HTTP 400: unknown model")):
            self.assertEqual(visuals.animate(self.still, "a dome", self.dest),
                             self.still)

    def test_a_headroom_failure_escalates_then_gives_up(self):
        err = RuntimeError("batch failed: CUDA out of memory")
        with mock.patch.object(visuals, "release_voice_vram"), \
             mock.patch.object(visuals, "_free_vram") as freed, \
             mock.patch.object(visuals.time, "sleep"), \
             mock.patch.object(visuals, "_dispatch_i2v", side_effect=err) as sent:
            self.assertEqual(visuals.animate(self.still, "a dome", self.dest),
                             self.still)
        self.assertEqual(sent.call_count, visuals.HEADROOM_RETRIES)
        # Recovery runs between attempts, not after the last one.
        self.assertEqual(freed.call_count, visuals.HEADROOM_RETRIES - 1)

    def test_the_narrator_is_evicted_before_dispatch(self):
        # The MoE holds two experts; the voice model's context alone can deny it
        # headroom on a 16GB card.
        with mock.patch.object(visuals, "release_voice_vram") as evicted, \
             mock.patch.object(visuals, "_dispatch_i2v", return_value="b1"), \
             mock.patch.object(visuals, "_collect_i2v", return_value=self.dest):
            visuals.animate(self.still, "a dome", self.dest)
        evicted.assert_called_once()


if __name__ == "__main__":
    unittest.main()
