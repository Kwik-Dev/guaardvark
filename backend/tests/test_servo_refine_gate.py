"""The second zoom pass is opt-in, and gated when it is on.

Background: on the 2026-09-21 blind-calibration session the refine pass turned a
6px median absolute X error into 54px and made the click worse on 10 of 15
attempts. These tests pin the three behaviours that followed from that
measurement — the pass is off by default, a refinement that disagrees wildly with
the anchor is discarded, and a refine y sitting at the crop's centre is treated as
an echo of the anchor rather than a measurement.
"""
import itertools
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

SCREEN = 1000
# yx order: [y1, x1, y2, x2]. Anchor centre lands at (x=300, y=700).
ANCHOR_MID = '[{"box_2d": [680, 280, 720, 320], "label": "t"}]'
# Anchor centre at (x=300, y=100) — high enough that the 300px crop clamps to
# the top edge, so the crop's centre y (150) differs from the anchor's y (100).
ANCHOR_TOP = '[{"box_2d": [80, 280, 120, 320], "label": "t"}]'


def _cfg(**over):
    cfg = {
        "has_vision": True, "vision_model": None, "internal_width": 1000,
        "scale_x": 1.0, "scale_y": 1.0, "offset_x": 0, "offset_y": 0,
        "coord_order": "yx", "disable_calibration": True,
    }
    cfg.update(over)
    return cfg


class RefineGateTest(unittest.TestCase):
    def setUp(self):
        self._archive = patch("backend.services.servo_controller.get_servo_archive")
        self._archive.start()
        self._dom = patch(
            "backend.services.servo_controller.ServoController._lookup_dom_coordinates",
            return_value=None,
        )
        self._dom.start()

    def tearDown(self):
        self._dom.stop()
        self._archive.stop()

    def _servo(self, responses, **cfg_over):
        from backend.services.servo_controller import ServoController
        screen = MagicMock()
        screen.screen_size.return_value = (SCREEN, SCREEN)
        results = []
        for text in responses:
            r = MagicMock()
            r.success, r.description, r.inference_ms = True, text, 100
            results.append(r)
        stream = itertools.chain(iter(results), itertools.repeat(results[-1]))
        analyzer = MagicMock()
        analyzer.default_model = "test-model"
        analyzer.analyze_fullsize.side_effect = lambda *a, **kw: next(stream)
        analyzer.analyze.side_effect = lambda *a, **kw: next(stream)
        return ServoController(screen, analyzer, vision_config=_cfg(**cfg_over)), analyzer

    def test_disabled_refine_never_makes_the_second_call(self):
        """The saving is the point: one inference per click, not two."""
        servo, analyzer = self._servo([ANCHOR_MID], disable_refine=True)
        coords = servo._estimate_coordinates(Image.new("RGB", (SCREEN, SCREEN)), "t")
        self.assertEqual(coords, (300, 700))
        self.assertEqual(analyzer.analyze_fullsize.call_count, 1)
        self.assertEqual(servo._last_parse_path, "anchor_only_refine_disabled")

    def test_reflex_default_is_off(self):
        """With no explicit vision_config override the reflex decides, and it says off."""
        servo, analyzer = self._servo([ANCHOR_MID])
        servo._estimate_coordinates(Image.new("RGB", (SCREEN, SCREEN)), "t")
        self.assertEqual(analyzer.analyze_fullsize.call_count, 1)
        self.assertEqual(servo._last_parse_path, "anchor_only_refine_disabled")

    def test_gate_keeps_anchor_when_refine_disagrees(self):
        # crop is (150,550)-(450,850); lx=900 maps to x=420, i.e. 120px off the
        # anchor's 300 — far beyond any anchor error ever measured.
        servo, _ = self._servo(
            [ANCHOR_MID, '[{"point": [900, 567], "label": "t"}]'], disable_refine=False)
        coords = servo._estimate_coordinates(Image.new("RGB", (SCREEN, SCREEN)), "t")
        self.assertEqual(coords, (300, 700))
        self.assertEqual(servo._last_parse_path, "anchor_refine_rejected")

    def test_gate_accepts_refine_inside_threshold(self):
        # lx=ly=567 maps to (320, 720): 20px from the anchor on both axes.
        servo, _ = self._servo(
            [ANCHOR_MID, '[{"point": [567, 567], "label": "t"}]'], disable_refine=False)
        coords = servo._estimate_coordinates(Image.new("RGB", (SCREEN, SCREEN)), "t")
        self.assertEqual(coords, (320, 720))
        self.assertEqual(servo._last_parse_path, "zoom_refinement")

    def test_y_echo_takes_the_anchors_y_not_the_crop_centre(self):
        # Anchor y=100, crop clamps to (0..300) so its centre y is 150. A refine
        # y of 500/1000 is the crop's centre — an echo. Without the guard the
        # click would drift to y=150; with it the anchor's 100 survives.
        servo, _ = self._servo(
            [ANCHOR_TOP, '[{"point": [567, 500], "label": "t"}]'], disable_refine=False)
        coords = servo._estimate_coordinates(Image.new("RGB", (SCREEN, SCREEN)), "t")
        self.assertEqual(coords, (320, 100))
        self.assertEqual(servo._last_parse_path, "zoom_refinement_y_echo")


class CalibrationIdentityTest(unittest.TestCase):
    """A Y-only fit must leave X untouched, exactly."""

    def test_bx_one_is_an_exact_x_identity(self):
        from backend.services.servo_controller import ServoController
        screen = MagicMock()
        screen.screen_size.return_value = (SCREEN, SCREEN)
        servo = ServoController(screen, MagicMock(), vision_config=_cfg())
        servo._calibration = {"model": "linear", "a_x": 0.0, "b_x": 1.0,
                              "a_y": 151.3, "b_y": 0.6929}
        for x in (0, 1, 137, 499, 500, 823, 999):
            cx, cy = servo._apply_calibration(x, 500)
            self.assertEqual(cx, x, f"X moved for x={x}")
        # And Y is actually corrected: the measured bias maps true 199 -> ~289.
        _, cy = servo._apply_calibration(300, 289)
        self.assertAlmostEqual(cy, 199, delta=2)


if __name__ == "__main__":
    unittest.main()


class DialectTest(unittest.TestCase):
    """The request dialect is part of the convention, and the parser reads all of them."""

    def _servo(self, **cfg_over):
        from backend.services.servo_controller import ServoController
        screen = MagicMock(); screen.screen_size.return_value = (SCREEN, SCREEN)
        az = MagicMock(); az.default_model = "test-model"
        return ServoController(screen, az, vision_config=_cfg(**cfg_over))

    def test_google_box2d_prompt_is_the_historical_string_verbatim(self):
        prompt, budget = self._servo()._anchor_request("red dot A")
        self.assertEqual(prompt, (
            "Detect the red dot A. Reply with ONLY a JSON list "
            '[{"box_2d": [y1, x1, y2, x2], "label": "red dot A"}] '
            "with coordinates normalized to 1000. If the target is not visible, "
            "reply with an empty list []."))
        self.assertEqual(budget, 128)

    def test_point_object_normalised(self):
        s = self._servo(internal_width=1000)
        self.assertEqual(s._parse_detection_response('{"x": 500, "y": 250}'), (500, 250))
        self.assertEqual(s._last_parse_path, "point_obj_normalized")

    def test_point_object_absolute_scales_from_the_image_sent(self):
        s = self._servo(internal_width=0)
        # a half-size image: 250 px there is 500 px on screen
        self.assertEqual(s._parse_detection_response('{"x": 250, "y": 125}', image_size=(500, 500)), (500, 250))
        self.assertEqual(s._last_parse_path, "point_obj")

    def test_bbox_absolute_on_the_screen_image_is_identity(self):
        s = self._servo(internal_width=0, coord_order="xy")
        got = s._parse_detection_response('[{"bbox_2d": [835, 267, 875, 307], "label": "t"}]',
                                          image_size=(SCREEN, SCREEN))
        self.assertEqual(got, (855, 287))

    def test_min_num_predict_from_the_convention_raises_the_budget(self):
        from backend.services.model_capability_resolver import CoordConvention
        s = self._servo()
        s._coords = CoordConvention(order="xy", grid=None, normalised=False, source="family",
                                    confidence=0.5, style="qwen_bbox2d_abs", min_num_predict=1024)
        s._vision_config["coord_style"] = "qwen_bbox2d_abs"
        prompt, budget = s._anchor_request("t")
        self.assertIn("bbox_2d", prompt)
        self.assertEqual(budget, 1024)
