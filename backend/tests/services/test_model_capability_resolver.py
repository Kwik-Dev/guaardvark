"""The resolver must believe Ollama, not a model's name.

Every case here is drawn from a real disagreement measured on 2026-09-22 across
the 23 models installed on the development box.
"""
import unittest
from unittest.mock import patch

from backend.services import model_capability_resolver as R


def _info(caps, arch="gemma4", ctx=8192, size=9000.0):
    return {"capabilities": list(caps), "architecture": arch,
            "native_context": ctx, "size_mb": size}


class VisionTruthTest(unittest.TestCase):
    def setUp(self):
        R.invalidate()

    def tearDown(self):
        R.invalidate()

    def test_a_gemma4_named_model_with_no_vision_tower_is_not_a_vision_model(self):
        """The case that makes name inference indefensible.

        VladimirGav/gemma4-26b-...-Uncensored is installed on this box, matches
        every "gemma4" pattern in the codebase, and has no vision capability.
        Handing it an image earns an Ollama 400.
        """
        with patch.object(R, "_info", return_value=_info(["completion", "tools", "thinking"])):
            self.assertFalse(R.sees_natively("VladimirGav/gemma4-26b-16GB-VRAM-Uncensored:latest"))

    def test_a_model_whose_name_says_nothing_can_still_see(self):
        """ministral-3:14b, devstral-small-2:24b, ornith-1.5:9b and the qwen3.6
        tags all report vision and were all being denied it."""
        with patch.object(R, "_info", return_value=_info(["completion", "vision", "tools"], "mistral3")):
            self.assertTrue(R.sees_natively("ministral-3:14b"))

    def test_absent_vision_tower_metadata_is_not_evidence_of_anything(self):
        """gemma4:12b reports the vision capability and exposes zero
        '.vision.' keys in model_info. Counting those keys would call it blind."""
        with patch.object(R, "_info", return_value=_info(["completion", "vision", "audio"], "gemma4")):
            self.assertTrue(R.sees_natively("gemma4:12b"))

    def test_name_is_used_only_when_ollama_cannot_answer_and_says_so(self):
        with patch.object(R, "_info", return_value=None):
            ok, evidence = R._vision_with_evidence("gemma4:e4b")
        self.assertTrue(ok)
        self.assertEqual(evidence, "name_guess_ollama_unreachable")

    def test_evidence_names_the_source_when_ollama_answers(self):
        with patch.object(R, "_info", return_value=_info(["completion", "vision"])):
            self.assertEqual(R._vision_with_evidence("anything")[1], "api_show_capabilities")


class CoordConventionTest(unittest.TestCase):
    def setUp(self):
        R.invalidate()

    def test_an_explicit_row_outranks_everything(self):
        c = R.coords_for("gemma4:e4b")
        self.assertEqual((c.order, c.grid, c.source), ("yx", 1000, "row"))
        self.assertEqual(c.confidence, 1.0)

    def test_unknown_model_gets_the_prompt_contract_not_a_silent_xy(self):
        """The servo's own prompt demands [y1,x1,y2,x2] normalised to 1000.

        Defaulting to "xy" contradicted the request the system had just made, and
        on the shipped default model it meant clicking with the axes swapped.
        Measured cost of that default on gemma4:e4b: 326px median error against
        63px read the right way.
        """
        with patch.object(R, "_info", return_value=_info(["completion", "vision"], "some-new-arch")), \
             patch.object(R, "_load_probe_store", return_value={}):
            c = R.coords_for("brand-new-vlm:70b")
        self.assertEqual(c.order, "yx")
        self.assertEqual(c.source, "prompt_contract")
        self.assertLess(c.confidence, 0.9, "a guess must not claim confidence")

    def test_family_default_beats_the_prompt_contract(self):
        with patch.object(R, "_info", return_value=_info(["completion", "vision"], "gemma4")), \
             patch.object(R, "_load_probe_store", return_value={}):
            c = R.coords_for("gemma4:some-unlisted-tag")
        self.assertEqual((c.order, c.source), ("yx", "family"))

    def test_a_measured_convention_beats_the_family_default(self):
        meas = {"screen": "1000x1000", "accuracy": None,
                "coords": {"style": "qwen_bbox2d_abs", "order": "xy", "grid": None, "confidence": 0.9}}
        with patch.object(R, "_info", return_value=_info(["completion", "vision"], "qwen3vl")), \
             patch.object(R, "_measurements", return_value=meas), \
             patch.object(R, "_load_probe_store", return_value={}):
            c = R.coords_for("qwen3-vl:8b", (1000, 1000))
        self.assertEqual((c.order, c.source, c.style, c.grid), ("xy", "probe", "qwen_bbox2d_abs", None))

    def test_the_old_probe_file_still_counts_but_says_so(self):
        legacy = {"qwen3-vl:8b": {"order": "xy", "grid": 1000, "confidence": 0.9}}
        with patch.object(R, "_info", return_value=_info(["completion", "vision"], "qwen3vl")), \
             patch.object(R, "_measurements", return_value={"screen": None, "coords": None, "accuracy": None}), \
             patch.object(R, "_load_probe_store", return_value=legacy):
            c = R.coords_for("qwen3-vl:8b")
        self.assertEqual((c.order, c.source), ("xy", "probe_legacy"))

    def test_a_recorded_probe_failure_names_the_dialects_it_tried(self):
        meas = {"screen": "1000x1000", "accuracy": None,
                "coords": {"order": None, "tried": [{"style": "google_box2d"}, {"style": "point_xy_abs"}]}}
        with patch.object(R, "_info", return_value=_info(["completion", "vision"], "mistral3")), \
             patch.object(R, "_measurements", return_value=meas), \
             patch.object(R, "_installed", return_value=[]):
            p = R.resolve("ministral-3:14b", "agent_screen", (1000, 1000))
        self.assertFalse(p.can_drive_screen)
        self.assertTrue(any("google_box2d" in b and "point_xy_abs" in b for b in p.blockers))


class EyeRankingTest(unittest.TestCase):
    """Measured accuracy decides; a fresh box falls through to the old order."""

    def setUp(self):
        R.invalidate()

    def _meas(self, table):
        def f(tag, screen=None):
            acc = table.get(tag)
            return {"screen": "1000x1000" if acc is not None else None,
                    "coords": None, "accuracy": {"median_px": acc} if acc is not None else None}
        return f

    def test_accuracy_mode_prefers_the_better_measured_eye(self):
        with patch.object(R, "_info", return_value=_info(["completion", "vision"])), \
             patch.object(R, "_measurements", side_effect=self._meas({"gemma4:e4b": 61.0, "qwen3.5:9b": 15.0})), \
             patch.object(R, "_available_vram_mb", return_value=None), \
             patch.dict("os.environ", {R.EYE_RANKING_ENV: "accuracy"}):
            order = [r["tag"] for r in R.rank_eyes(["gemma4:e4b", "qwen3.5:9b"], (1000, 1000))]
        self.assertEqual(order[0], "qwen3.5:9b")

    def test_confidence_mode_is_the_old_order(self):
        with patch.object(R, "_info", return_value=_info(["completion", "vision"])), \
             patch.object(R, "_measurements", side_effect=self._meas({"gemma4:e4b": 61.0, "qwen3.5:9b": 15.0})), \
             patch.object(R, "_available_vram_mb", return_value=None), \
             patch.dict("os.environ", {R.EYE_RANKING_ENV: "confidence"}):
            order = [r["tag"] for r in R.rank_eyes(["gemma4:e4b", "qwen3.5:9b"], (1000, 1000))]
        self.assertEqual(order[0], "gemma4:e4b", "row confidence 1.0 outranks a family default")

    def test_nothing_measured_falls_through_to_confidence(self):
        with patch.object(R, "_info", return_value=_info(["completion", "vision"])), \
             patch.object(R, "_measurements", side_effect=self._meas({})), \
             patch.object(R, "_available_vram_mb", return_value=None), \
             patch.dict("os.environ", {R.EYE_RANKING_ENV: "accuracy"}):
            order = [r["tag"] for r in R.rank_eyes(["qwen3.5:9b", "gemma4:e4b"], (1000, 1000))]
        self.assertEqual(order[0], "gemma4:e4b", "a fresh clone must behave as before")

    def test_an_eye_that_does_not_fit_is_demoted_not_dropped(self):
        def info(tag):
            return _info(["completion", "vision"], size=9600.0 if tag == "gemma4:e4b" else 6600.0)
        with patch.object(R, "_info", side_effect=info), \
             patch.object(R, "_measurements", side_effect=self._meas({"gemma4:e4b": 15.0, "qwen3.5:9b": 61.0})), \
             patch.object(R, "_available_vram_mb", return_value=8000.0), \
             patch.dict("os.environ", {R.EYE_RANKING_ENV: "accuracy"}):
            rows = R.rank_eyes(["gemma4:e4b", "qwen3.5:9b"], (1000, 1000))
        self.assertEqual([r["tag"] for r in rows], ["qwen3.5:9b", "gemma4:e4b"])
        self.assertIs(rows[1]["fits"], False)


class EyesTest(unittest.TestCase):
    def setUp(self):
        R.invalidate()

    def test_a_sighted_model_borrows_nothing(self):
        with patch.object(R, "_info", return_value=_info(["completion", "vision"])):
            e = R.eyes_for("gemma4:e4b")
        self.assertIsNone(e.model)
        self.assertEqual(e.mechanism, "native")

    def test_a_blind_model_is_lent_an_eye_that_is_already_loaded(self):
        def info(tag):
            return _info(["completion", "vision"]) if tag != "magistral:24b" \
                else _info(["completion", "tools"], "llama")
        with patch.object(R, "_info", side_effect=info), \
             patch.object(R, "_installed", return_value=["magistral:24b", "gemma4:e4b", "qwen3.5:9b"]), \
             patch.object(R, "_resident", return_value=["qwen3.5:9b"]):
            e = R.eyes_for("magistral:24b")
        self.assertEqual(e.model, "qwen3.5:9b")
        self.assertEqual(e.mechanism, "sibling_vlm")
        self.assertIn("already loaded", e.reason)

    def test_no_eye_available_is_reported_as_a_blocker_not_a_crash(self):
        with patch.object(R, "_info", return_value=_info(["completion", "tools"], "llama")), \
             patch.object(R, "_installed", return_value=["magistral:24b"]), \
             patch.object(R, "_resident", return_value=[]):
            p = R.resolve("magistral:24b", "agent_screen")
        self.assertFalse(p.can_drive_screen)
        self.assertTrue(any("No vision-capable model" in b for b in p.blockers))


class ResolveTest(unittest.TestCase):
    def setUp(self):
        R.invalidate()

    def test_a_sighted_model_with_a_known_convention_can_drive_the_screen(self):
        with patch.object(R, "_info", return_value=_info(["completion", "vision", "tools"])):
            p = R.resolve("gemma4:e4b", "agent_screen")
        self.assertTrue(p.can_drive_screen)
        self.assertEqual(p.blockers, ())
        self.assertTrue(p.supports_tools)

    def test_unknown_surface_is_refused_rather_than_silently_accepted(self):
        with self.assertRaises(ValueError):
            R.resolve("gemma4:e4b", "teleport")

    def test_invalidate_drops_the_cache(self):
        with patch.object(R, "_info", return_value=_info(["completion", "vision"])):
            R.resolve("x:1", "chat")
            self.assertTrue(R._cache)
            R.invalidate()
            self.assertFalse(R._cache)


if __name__ == "__main__":
    unittest.main()
