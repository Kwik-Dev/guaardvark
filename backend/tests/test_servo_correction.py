"""The servo's correction loop: strict judgments, a narrowing box, and modes
that never change the click unless asked to."""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from PIL import Image

from backend.services.servo_controller import ServoController, CorrectionOutcome
from backend.services.model_capability_resolver import CoordConvention


def _conv():
    return CoordConvention(order="yx", grid=1000, normalised=True, source="test", confidence=0.9,
                           style="google_box2d", min_num_predict=128)


def _analyzer(responses):
    """analyze_fullsize answers in order, then repeats the last."""
    import itertools
    results = []
    for text in responses:
        r = MagicMock()
        r.success = True
        r.description = text
        r.inference_ms = 5
        results.append(r)
    it = itertools.chain(iter(results), itertools.repeat(results[-1]))
    a = MagicMock()
    a.default_model = "eye-under-test"
    a.analyze_fullsize.side_effect = lambda *args, **kw: next(it)
    a.analyze.side_effect = lambda *args, **kw: next(it)
    return a


def _screen(size=(1000, 1000)):
    s = MagicMock()
    s.capture.return_value = (Image.new("RGB", size, (90, 90, 90)), (0, 0))
    s.move.return_value = {"success": True}
    s.click.return_value = {"success": True}
    s.screen_size.return_value = size
    return s


def _servo(responses, mode=None, accuracy=None, explicit_env=None, reflex_mode="shadow", screen=None, judge=None):
    cfg = {"coord_order": "yx", "internal_width": 1000, "coord_style": "google_box2d"}
    if mode is not None:
        cfg["correction_mode"] = mode
    env = {"GUAARDVARK_SERVO_CORRECTION": explicit_env} if explicit_env else {}
    real_get_reflex = __import__("backend.services.servo_knowledge_store", fromlist=["get_reflex"]).get_reflex

    def reflex(name, default=None):
        if name == "correction_mode":
            return reflex_mode
        return real_get_reflex(name, default)

    with patch("backend.services.servo_controller.get_reflex", side_effect=reflex), \
         patch("backend.services.servo_knowledge_store.load_servo_calibration", return_value=None), \
         patch.dict(os.environ, env, clear=False):
        if not explicit_env:
            os.environ.pop("GUAARDVARK_SERVO_CORRECTION", None)
        servo = ServoController(screen or _screen(), _analyzer(responses), vision_config=cfg,
                                eye_accuracy_px=accuracy, eye_judge_rate=judge)
    return servo


ANCHOR = '[{"box_2d": [400, 400, 440, 440], "label": "dot"}]'   # yx/1000 → centre (420, 420)


class ParseJudgmentTest(unittest.TestCase):
    def test_valid_answers_parse(self):
        p = ServoController._parse_relative_judgment
        self.assertEqual(p('{"visible": true, "dx": "left", "dy": "same"}'), {"visible": True, "dx": "left", "dy": "same"})
        self.assertEqual(p('Sure: {"visible": false}'), {"visible": False, "dx": None, "dy": None})

    def test_swapped_fields_are_read_by_the_words(self):
        # gemma4:e2b puts the vertical word in dx and the horizontal in dy.
        p = ServoController._parse_relative_judgment
        self.assertEqual(p('{"visible": true, "dx": "below", "dy": "right"}'),
                         {"visible": True, "dx": "right", "dy": "below"})
        self.assertEqual(p('{"visible": true, "dx": "above", "dy": "same"}'),
                         {"visible": True, "dx": "same", "dy": "above"})

    def test_anything_else_is_none_never_a_default(self):
        p = ServoController._parse_relative_judgment
        for text in ("", "down", '{"dx": "left", "dy": "same"}', '{"visible": "yes", "dx": "left", "dy": "same"}',
                     '{"visible": true, "dx": "up", "dy": "same"}', '{"visible": true, "dx": "left", "dy": "north"}',
                     '{"visible": true, "dx": "left"}', "[1, 2]"):
            self.assertIsNone(p(text), text)


class BoxTest(unittest.TestCase):
    def test_same_collapses_to_twice_the_target_width(self):
        # An eye's "same" means within about a target's width, so the centre
        # must stay inside the box for a later call to reach it.
        self.assertEqual(ServoController._update_axis(0, 200, 100, "same", 0.55, 24), (76, 124))

    def test_a_side_call_keeps_part_of_the_discarded_half(self):
        lo, hi = ServoController._update_axis(0, 200, 100, "left", 0.55, 24)
        self.assertEqual((lo, hi), (0, 145))
        lo, hi = ServoController._update_axis(0, 200, 100, "below", 0.55, 24)
        self.assertAlmostEqual(lo, 55)
        self.assertEqual(hi, 200)

    def test_seed_box_extends_away_from_centre_on_y_only(self):
        servo = _servo([ANCHOR], accuracy=50.0)
        box = servo._seed_box((500, 800))          # below centre by 300
        # ±1.15×50 = 57.5 each way; Y extended downward by 1.15×300×(1/0.69−1) ≈ 155
        self.assertAlmostEqual(box[0], 442.5)
        self.assertAlmostEqual(box[2], 557.5)
        self.assertAlmostEqual(box[1], 742.5)
        self.assertAlmostEqual(box[3], min(969, 857.5 + 1.15 * 300 * (1 / 0.69 - 1)), places=0)


class ArmingTest(unittest.TestCase):
    def setUp(self):
        self._sleep = patch("time.sleep")
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()

    def _arm(self, servo, single_attempt=False, precision=None):
        servo._last_detection_source = "vision"
        return servo._should_correct("dot", single_attempt, precision, Image.new("RGB", (1000, 1000), (90, 90, 90)))

    def test_mode_off_never_arms(self):
        self.assertEqual(self._arm(_servo([ANCHOR], mode="off")), (False, "mode_off"))

    def test_env_and_config_precedence(self):
        self.assertEqual(_servo([ANCHOR], explicit_env="on").correction_mode, "on")
        self.assertEqual(_servo([ANCHOR], mode="off", explicit_env="on").correction_mode, "off")
        self.assertEqual(_servo([ANCHOR], reflex_mode="off").correction_mode, "off")

    def test_an_accurate_eye_needs_no_second_look(self):
        armed, why = self._arm(_servo([ANCHOR], accuracy=13.0))
        self.assertFalse(armed)
        self.assertEqual(why, "eye_accurate(13px<=24px)")

    def test_a_coarse_or_unmeasured_eye_arms(self):
        self.assertEqual(self._arm(_servo([ANCHOR], accuracy=54.0)), (True, "eye_coarse(54px>24px)"))
        self.assertEqual(self._arm(_servo([ANCHOR])), (True, "eye_unmeasured"))

    def test_precision_overrides_the_accuracy_gate_both_ways(self):
        self.assertEqual(self._arm(_servo([ANCHOR], accuracy=13.0), precision=True), (True, "precision_requested"))
        self.assertEqual(self._arm(_servo([ANCHOR]), precision=False), (False, "precision_off"))

    def test_training_runs_arm_only_when_the_mode_was_set_for_them(self):
        self.assertEqual(self._arm(_servo([ANCHOR]), single_attempt=True), (False, "single_attempt"))
        self.assertTrue(self._arm(_servo([ANCHOR], mode="shadow"), single_attempt=True)[0])

    def test_dom_calibration_and_cap_disarm(self):
        s = _servo([ANCHOR])
        s._last_detection_source = "dom"
        self.assertEqual(s._should_correct("dot", False, None, None), (False, "dom_sourced"))
        s = _servo([ANCHOR])
        s._vision_config["disable_calibration"] = True
        self.assertEqual(self._arm(s), (False, "calibration_run"))
        s = _servo([ANCHOR])
        s._corrections_armed_this_session = 12
        self.assertEqual(self._arm(s), (False, "session_cap(12)"))

    def test_an_animating_screen_refuses_the_probes(self):
        screen = _screen()
        frames = iter([(Image.new("RGB", (1000, 1000), (90, 90, 90)), (0, 0)),
                       (Image.new("RGB", (1000, 1000), (200, 200, 200)), (0, 0))])
        screen.capture.side_effect = lambda: next(frames)
        s = _servo([ANCHOR], screen=screen)
        first, _ = screen.capture()
        s._last_detection_source = "vision"
        self.assertEqual(s._should_correct("dot", False, None, first), (False, "screen_animating"))


@patch("backend.services.servo_controller.get_servo_archive")
@patch("time.sleep")
class LoopTest(unittest.TestCase):
    """Anchor at (420, 420); the eye says the dot is right-and-below, then same."""

    def test_shadow_runs_the_loop_and_clicks_the_estimate(self, _sleep, mock_archive):
        s = _servo([ANCHOR, '{"visible": true, "dx": "right", "dy": "below"}',
                    '{"visible": true, "dx": "same", "dy": "same"}'], mode="shadow", accuracy=54.0)
        r = s.click_target("dot")
        self.assertEqual((r["x"], r["y"]), (420, 420))
        s.screen.click.assert_called_once_with(420, 420, button="left")
        c = r["correction"]
        self.assertEqual((c["mode"], c["armed_reason"], c["stop_reason"], c["applied"]),
                         ("shadow", "eye_coarse(54px>24px)", "on_target", False))
        self.assertEqual(c["steps"], 2)
        self.assertGreater(c["drift_px"], 0)
        self.assertEqual(r["corrections"], 2)
        rec = mock_archive.return_value.record.call_args.kwargs
        self.assertEqual(rec["correction"]["stop_reason"], "on_target")
        self.assertEqual([m["direction"] for m in rec["correction_log"]], ["right_and_down", "on_target"])

    def test_on_clicks_the_final(self, _sleep, mock_archive):
        s = _servo([ANCHOR, '{"visible": true, "dx": "right", "dy": "below"}',
                    '{"visible": true, "dx": "same", "dy": "same"}'], mode="on", accuracy=54.0)
        r = s.click_target("dot")
        self.assertTrue(r["correction"]["applied"])
        fx, fy = r["x"], r["y"]
        self.assertGreater(fx, 420)
        self.assertGreater(fy, 420)
        s.screen.click.assert_called_once_with(fx, fy, button="left")

    def test_unparseable_stops_and_leaves_the_estimate(self, _sleep, mock_archive):
        s = _servo([ANCHOR, "I think it is somewhere down there"], mode="on", accuracy=54.0)
        r = s.click_target("dot")
        c = r["correction"]
        self.assertEqual((c["stop_reason"], c["applied"], c["final"], c["unparsed"]),
                         ("unparseable", False, [420, 420], 1))
        s.screen.click.assert_called_once_with(420, 420, button="left")

    def test_not_visible_stops(self, _sleep, mock_archive):
        s = _servo([ANCHOR, '{"visible": false}'], mode="on", accuracy=54.0)
        r = s.click_target("dot")
        self.assertEqual(r["correction"]["stop_reason"], "not_visible")
        self.assertFalse(r["correction"]["applied"])

    def test_two_reversals_on_an_axis_stop(self, _sleep, mock_archive):
        s = _servo([ANCHOR, '{"visible": true, "dx": "left", "dy": "same"}',
                    '{"visible": true, "dx": "right", "dy": "same"}',
                    '{"visible": true, "dx": "left", "dy": "same"}',
                    '{"visible": true, "dx": "right", "dy": "same"}'], mode="shadow", accuracy=54.0)
        r = s.click_target("dot")
        self.assertEqual(r["correction"]["stop_reason"], "oscillating_x")

    def test_the_budget_is_sized_to_narrow_the_box(self, _sleep, mock_archive):
        # One direction every time: the box shrinks until it is target-sized,
        # which four fixed steps could not reach from a coarse eye's box.
        s = _servo([ANCHOR, '{"visible": true, "dx": "right", "dy": "below"}'], mode="shadow", accuracy=54.0)
        r = s.click_target("dot")
        self.assertEqual(r["correction"]["stop_reason"], "converged")
        self.assertGreater(r["correction"]["steps"], 5)

    def test_auto_is_on_for_a_measured_good_judge(self, _sleep, mock_archive):
        s = _servo([ANCHOR], reflex_mode="auto", accuracy=54.0, judge=1.0)
        self.assertEqual(s.correction_mode, "on")

    def test_auto_is_shadow_for_an_unmeasured_judge(self, _sleep, mock_archive):
        s = _servo([ANCHOR], reflex_mode="auto", accuracy=54.0)
        self.assertEqual(s.correction_mode, "shadow")

    def test_an_explicit_mode_beats_auto(self, _sleep, mock_archive):
        s = _servo([ANCHOR], mode="shadow", reflex_mode="auto", accuracy=54.0, judge=1.0)
        self.assertEqual(s.correction_mode, "shadow")

    def test_the_shipped_default_is_auto(self, _sleep, mock_archive):
        from backend.services.servo_knowledge_store import get_reflex
        self.assertEqual(get_reflex("correction_mode"), "auto")

    def test_an_eye_that_judges_poorly_is_not_armed(self, _sleep, mock_archive):
        s = _servo([ANCHOR, '{"visible": true, "dx": "right", "dy": "below"}'], mode="on", accuracy=240.0)
        s.eye_judge_rate = 0.76
        r = s.click_target("dot")
        self.assertIsNone(r.get("correction"))
        s.screen.click.assert_called_once_with(420, 420, button="left")

    def test_an_eye_that_judges_well_is_armed(self, _sleep, mock_archive):
        s = _servo([ANCHOR, '{"visible": true, "dx": "same", "dy": "same"}'], mode="on", accuracy=54.0)
        s.eye_judge_rate = 1.0
        r = s.click_target("dot")
        self.assertEqual(r["correction"]["stop_reason"], "on_target")

    def test_the_cap_bounds_the_budget(self, _sleep, mock_archive):
        s = _servo([ANCHOR, '{"visible": true, "dx": "right", "dy": "below"}'], mode="shadow", accuracy=54.0)
        real = __import__("backend.services.servo_knowledge_store", fromlist=["get_reflex"]).get_reflex
        cap = lambda name, default=None: 2 if name == "correction_max_steps_cap" else real(name, default)
        with patch("backend.services.servo_controller.get_reflex", side_effect=cap):
            r = s.click_target("dot")
        self.assertEqual(r["correction"]["stop_reason"], "max_steps")
        self.assertEqual(r["correction"]["steps"], 3)   # probe 0 + 2

    def test_not_visible_looks_once_across_the_screen(self, _sleep, mock_archive):
        s = _servo([ANCHOR, '{"visible": false}', '{"visible": true, "dx": "same", "dy": "same"}',
                    '{"visible": true, "dx": "same", "dy": "same"}'], mode="shadow", accuracy=54.0)
        r = s.click_target("dot")
        self.assertEqual(r["correction"]["stop_reason"], "on_target")
        self.assertFalse(r["correction"]["steps"] == 1)

    def test_a_wide_view_same_is_asked_again_close_up(self, _sleep, mock_archive):
        # A 300px eye seeds a box too wide for a close look; its first "same"
        # narrows the box and only the close-up "same" ends the search.
        s = _servo([ANCHOR, '{"visible": true, "dx": "same", "dy": "same"}'], mode="shadow", accuracy=300.0)
        r = s.click_target("dot")
        self.assertEqual(r["correction"]["stop_reason"], "on_target")
        self.assertEqual(r["correction"]["steps"], 2)

    def test_a_probe_that_cannot_finish_is_not_started(self, _sleep, mock_archive):
        s = _servo([ANCHOR, '{"visible": true, "dx": "right", "dy": "below"}'], mode="shadow", accuracy=54.0)
        clock = iter([0.0, 0.0, 0.0, 3.0, 3.0, 3.0, 3.1, 3.1] + [10.0] * 20)
        with patch("backend.services.servo_controller.time.monotonic", side_effect=lambda: next(clock)):
            r = s.click_target("dot")
        self.assertEqual(r["correction"]["stop_reason"], "deadline")

    def test_the_session_cap_disarms_after_enough_armed_clicks(self, _sleep, mock_archive):
        s = _servo([ANCHOR, '{"visible": true, "dx": "same", "dy": "same"}'], mode="shadow", accuracy=54.0)
        s._corrections_armed_this_session = 11
        self.assertIsNotNone(s.click_target("dot")["correction"])
        self.assertIsNone(s.click_target("dot")["correction"])
        self.assertEqual(s._last_correction_skip, "session_cap(12)")
        self.assertEqual(mock_archive.return_value.record.call_args.kwargs["correction_skip"], "session_cap(12)")

    def test_drift_is_clamped(self, _sleep, mock_archive):
        # Anchor at the centre of the screen: |v| = 0 so the bound is the eye's own noise.
        s = _servo(['[{"box_2d": [490, 490, 510, 510]}]', '{"visible": true, "dx": "right", "dy": "below"}',
                    '{"visible": true, "dx": "right", "dy": "below"}', '{"visible": true, "dx": "right", "dy": "below"}',
                    '{"visible": true, "dx": "right", "dy": "below"}', '{"visible": true, "dx": "right", "dy": "below"}'],
                   mode="on", accuracy=54.0)
        r = s.click_target("dot")
        self.assertTrue(r["correction"]["clamped"])
        self.assertLessEqual(r["correction"]["drift_px"], 54.5)


if __name__ == "__main__":
    unittest.main()
