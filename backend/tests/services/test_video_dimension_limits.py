#!/usr/bin/env python3
"""Video dimension guard rails (2026-08-08, aspect-selector regression).

Full HD + Square on the video page sent 1920×1920 (3.7 MPx) to the Wan
workflows — both the 5B and 14B ran until the watchdog timeout, which read as
"the aspect selector is broken". The frontend now caps pixel area per model,
and the backend mirrors it here as defense-in-depth because batch retry_data
replays old width/height verbatim.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services.comfyui_video_generator import ComfyUIVideoGenerator  # noqa: E402


class TestClampPixelArea(unittest.TestCase):

    def test_proven_wan_dims_pass_untouched(self):
        # 1280×736 completed 22/22 on wan22-5b — must never be scaled down
        self.assertEqual(
            ComfyUIVideoGenerator._clamp_pixel_area(1280, 736, "wan22-5b"),
            (1280, 736),
        )
        self.assertEqual(
            ComfyUIVideoGenerator._clamp_pixel_area(832, 480, "wan22-14b-i2v"),
            (832, 480),
        )

    def test_fullhd_square_is_scaled_down(self):
        w, h = ComfyUIVideoGenerator._clamp_pixel_area(1920, 1920, "wan22-5b")
        self.assertLessEqual(w * h, 1_050_000)
        # aspect preserved (square stays square)
        self.assertEqual(w, h)

    def test_fullhd_widescreen_preserves_aspect(self):
        w, h = ComfyUIVideoGenerator._clamp_pixel_area(1920, 1080, "wan22-14b")
        self.assertLessEqual(w * h, 1_050_000)
        self.assertAlmostEqual(w / h, 1920 / 1080, places=2)

    def test_ltx_budget(self):
        # LTX native (and the frontend's aspect-refit dims) stay untouched
        self.assertEqual(
            ComfyUIVideoGenerator._clamp_pixel_area(768, 512, "ltx23-distilled-fp8"),
            (768, 512),
        )
        w, h = ComfyUIVideoGenerator._clamp_pixel_area(1920, 1920, "ltx23-distilled-fp8")
        self.assertLessEqual(w * h, 1_050_000)

    def test_unbudgeted_family_is_untouched(self):
        self.assertEqual(
            ComfyUIVideoGenerator._clamp_pixel_area(1920, 1920, "cogvideox-5b"),
            (1920, 1920),
        )

    def test_clamp_then_align_stays_within_budget(self):
        w, h = ComfyUIVideoGenerator._clamp_pixel_area(1920, 1920, "wan22-5b")
        w, h = ComfyUIVideoGenerator._align_dimensions(w, h, "wan22-5b")
        self.assertEqual(w % 32, 0)
        self.assertEqual(h % 32, 0)
        # alignment rounding must not blow meaningfully past the cap
        self.assertLessEqual(w * h, 1_100_000)


if __name__ == "__main__":
    unittest.main()
