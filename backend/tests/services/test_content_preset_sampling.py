#!/usr/bin/env python3
"""Content presets shape prompts; they must never choose sampling (2026-08-07).

They used to carry SD 1.5-era recommended_steps / recommended_guidance /
recommended_dimensions (35 steps, CFG 8.0, 512x768). Because they are keyed on
*content type*, they knew nothing about the selected model — typing a full-body
prompt pushed the UI to 35 steps even for Krea 2 Turbo, an 8-step CFG-free model.
The backend clamped it back at render time, so the panel displayed numbers that
never ran. Observed: the auto-detector was choosing 35 steps and not helping anything.

Sampling now belongs solely to the model, via MODEL_SETTINGS in settings_validator.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services.offline_image_generator import (  # noqa: E402
    OfflineImageGenerator,
    ImageGenerationRequest,
)
from backend.services.settings_validator import MODEL_SETTINGS  # noqa: E402

_SAMPLING_KEYS = ("recommended_steps", "recommended_guidance", "recommended_dimensions")


class TestContentPresetsCarryNoSampling(unittest.TestCase):

    def setUp(self):
        self.gen = OfflineImageGenerator()

    def test_no_preset_recommends_sampling(self):
        for name, preset in self.gen.content_presets.items():
            for key in _SAMPLING_KEYS:
                self.assertNotIn(
                    key, preset,
                    f"content preset '{name}' must not choose {key} — it cannot know "
                    "which model will render the prompt",
                )

    def test_presets_still_shape_the_prompt(self):
        """The half that was kept: suffixes and the curated negative blocks."""
        for name, preset in self.gen.content_presets.items():
            self.assertTrue(
                preset.get("positive_suffix") or preset.get("negative_prompt"),
                f"content preset '{name}' has nothing left to contribute",
            )

    def test_full_body_prompt_still_detected(self):
        detection = self.gen.detect_content_type(
            "full body photo of a woman standing in a studio"
        )
        self.assertEqual(detection.get("recommended_preset"), "person_full_body")

    def test_enhancement_still_produces_prompt_and_negatives(self):
        enhanced, negative, detection = self.gen.enhance_prompt_for_quality(
            prompt="full body photo of a woman standing",
            style="realistic",
            auto_enhance=True,
        )
        self.assertIn("full body photo of a woman standing", enhanced)
        self.assertTrue(negative.strip())
        self.assertEqual(detection.get("recommended_preset"), "person_full_body")


class TestModelOwnsSampling(unittest.TestCase):
    """Per-model recipes are the single source of truth for steps/guidance."""

    def test_turbo_models_keep_their_own_recipes(self):
        self.assertEqual(MODEL_SETTINGS["krea2-turbo"]["recommended_steps"], 8)
        self.assertEqual(MODEL_SETTINGS["krea2-turbo"]["recommended_guidance"], 0.0)
        self.assertEqual(MODEL_SETTINGS["krea2-raw"]["recommended_steps"], 52)
        self.assertEqual(MODEL_SETTINGS["krea2-raw"]["recommended_guidance"], 3.5)

    def test_krea_turbo_is_never_pushed_to_35_steps(self):
        """The reported symptom, pinned end to end."""
        gen = OfflineImageGenerator()
        request = ImageGenerationRequest(prompt="full body photo", model="krea2-turbo")
        request.num_inference_steps = 35   # what the old auto-detector set
        request.guidance_scale = 8.0
        gen._soft_clamp_family_sampling(request, "krea2")

        self.assertEqual(request.num_inference_steps, 8)
        self.assertEqual(request.guidance_scale, 0.0)

    def test_user_chosen_steps_inside_the_envelope_survive(self):
        """Clamping must not become its own placebo — real choices still apply."""
        gen = OfflineImageGenerator()
        request = ImageGenerationRequest(prompt="x", model="krea2-turbo")
        request.num_inference_steps = 12
        request.guidance_scale = 0.0
        gen._soft_clamp_family_sampling(request, "krea2")

        self.assertEqual(request.num_inference_steps, 12)


if __name__ == "__main__":
    unittest.main(verbosity=2)
