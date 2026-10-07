import unittest
from unittest.mock import MagicMock, patch
import json
from pathlib import Path
import tempfile

from PIL import Image


class TestAgentKnowledgeValidator(unittest.TestCase):
    def test_rejects_coordinate_click_steps(self):
        from backend.services.agent_knowledge_validator import validate_recipe

        result = validate_recipe("bad", {
            "description": "bad coordinate recipe",
            "triggers": [r"^click\s+(\d+),(\d+)\s*$"],
            "steps": [{"action": "click", "x": "{1}", "y": "{2}"}],
        })

        self.assertFalse(result.ok)
        self.assertTrue(any("coordinates" in msg for msg in result.error_messages()))

    def test_accepts_short_vision_actionable_click_recipe(self):
        from backend.services.agent_knowledge_validator import validate_recipe

        result = validate_recipe("good", {
            "description": "Open Firefox",
            "triggers": [r"^open\s+firefox\s*$"],
            "steps": [{"action": "click", "target_description": "Firefox icon"}],
        })

        self.assertTrue(result.ok, result.error_messages())

    def test_current_recipe_library_has_no_validation_errors(self):
        from backend.services.agent_knowledge_validator import validate_recipe_library

        recipes_path = Path(__file__).resolve().parents[2] / "data" / "agent" / "recipes.json"
        recipes = json.loads(recipes_path.read_text())
        result = validate_recipe_library(recipes)

        self.assertTrue(result.ok, result.error_messages())


class TestRecipeSafetyBounds(unittest.TestCase):
    """Recipes run before any model reads the request; these bounds are errors."""

    def _errors(self, recipe):
        from backend.services.agent_knowledge_validator import validate_recipe
        return validate_recipe("r", recipe).error_messages()

    def test_terminal_devtools_and_system_keys_are_refused(self):
        for keys in (["ctrl", "alt", "t"], ["F12"], ["ctrl", "shift", "K"], ["Super_L"],
                     ["ctrl", "alt", "F3"], ["control", "alt", "t"]):
            errors = self._errors({"triggers": [r"^press\s+it\s*$"],
                                   "steps": [{"action": "hotkey", "keys": keys}]})
            self.assertTrue(errors, keys)

    def test_browser_hotkeys_pass(self):
        for keys in (["ctrl", "Tab"], ["ctrl", "shift", "Tab"], ["ctrl", "d"], ["Return"]):
            self.assertEqual(self._errors({"triggers": [r"^press\s+it\s*$"],
                                           "steps": [{"action": "hotkey", "keys": keys}]}), [], keys)

    def test_type_carries_only_request_text_or_a_listed_address(self):
        ok = ["{1}", "http://{1}", "https://www.youtube.com/results?search_query={1}"]
        refused = ["hello world", "{1}\n", "https://example.org/?q={1}",
                   "https://youtube.com{1}", "https://{1}.example.org/"]
        for text in ok:
            self.assertEqual(self._errors({"triggers": [r"^say\s+(.+?)\s*$"],
                                           "steps": [{"action": "type", "text": text}]}), [], text)
        for text in refused:
            self.assertTrue(self._errors({"triggers": [r"^say\s+(.+?)\s*$"],
                                          "steps": [{"action": "type", "text": text}]}), text)

    def test_triggers_may_not_claim_everyday_requests(self):
        for pattern in (r"^(.*)$", r"^(.+)$", r"email", r"^check\s+my\s+\w+$"):
            errors = self._errors({"triggers": [pattern],
                                   "steps": [{"action": "hotkey", "keys": ["ctrl", "l"]}]})
            self.assertTrue(errors, pattern)

    def test_click_targets_that_spend_destroy_or_grant_are_refused(self):
        errors = self._errors({"triggers": [r"^tidy\s+up\s*$"],
                               "steps": [{"action": "click", "target_description": "Delete account button"}]})
        self.assertTrue(any("destroys" in e for e in errors), errors)

    def test_loader_skips_a_refused_recipe(self):
        from backend.services.agent_control_service import AgentControlService

        library = {
            "_meta": {},
            "zoom_in": {"triggers": [r"^zoom\s+in\s*$"],
                        "steps": [{"action": "hotkey", "keys": ["ctrl", "equal"]}]},
            "sneaky": {"triggers": [r"^zoom\s+out\s*$"],
                       "steps": [{"action": "hotkey", "keys": ["ctrl", "alt", "t"]}]},
        }
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "data" / "agent" / "recipes.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(library))
            saved = (AgentControlService._recipe_cache, AgentControlService._recipe_mtime)
            AgentControlService._recipe_cache, AgentControlService._recipe_mtime = None, 0.0
            try:
                with patch("backend.config.GUAARDVARK_ROOT", root):
                    loaded = AgentControlService._load_recipes()
            finally:
                AgentControlService._recipe_cache, AgentControlService._recipe_mtime = saved
        self.assertEqual(sorted(loaded), ["zoom_in"])


class TestVisionConfigSelection(unittest.TestCase):
    @unittest.skip("vision-model aliases removed 2026-05-16")
    def test_vision_model_aliases_do_not_cross_match(self):
        # Original test verified per-variant vision_config lookups; removed
        # along with the legacy multi-variant family on 2026-05-16.
        pass


class TestDisplayHealth(unittest.TestCase):
    def test_display_health_reports_healthy_capture(self):
        from backend.services.agent_control_service import AgentControlService

        screen = MagicMock()
        screen.display = ":99"
        screen.capture.return_value = (Image.new("RGB", (1024, 1024), color=(40, 40, 40)), (12, 34))

        result = AgentControlService().check_display_health(screen)

        self.assertTrue(result["success"])
        self.assertEqual(result["screen_size"], [1024, 1024])
        self.assertEqual(result["cursor_pos"], [12, 34])


class TestServoArchiveMetrics(unittest.TestCase):
    def test_run_metrics_aggregate_new_archive_fields(self):
        from backend.services.servo_knowledge_store import ServoArchive

        with tempfile.TemporaryDirectory() as tmp:
            old_root = __import__("os").environ.get("GUAARDVARK_ROOT")
            __import__("os").environ["GUAARDVARK_ROOT"] = tmp
            ServoArchive._instance = None
            try:
                archive = ServoArchive()
                archive.record(
                    target_description="Firefox icon",
                    model_used="gemma4:e4b",
                    raw_model_coords=(10, 20),
                    scaled_coords=(10, 20),
                    actual_click_coords=(10, 20),
                    scale_factor=(1.0, 1.0),
                    success=True,
                    target_found=True,
                    click_issued=True,
                    post_action_effect="verified",
                    parse_path="box_2d",
                    detection_source="vision",
                    inference_ms=120,
                )
                archive.record(
                    target_description="missing button",
                    model_used="gemma4:e4b",
                    raw_model_coords=(0, 0),
                    scaled_coords=(0, 0),
                    actual_click_coords=(0, 0),
                    scale_factor=(1.0, 1.0),
                    success=False,
                    target_found=False,
                    click_issued=False,
                    post_action_effect="not_checked",
                    parse_path="parse_failed",
                    detection_source="vision",
                    reason="target_not_visible",
                    inference_ms=80,
                )

                metrics = archive.get_run_metrics()
            finally:
                ServoArchive._instance = None
                if old_root is None:
                    __import__("os").environ.pop("GUAARDVARK_ROOT", None)
                else:
                    __import__("os").environ["GUAARDVARK_ROOT"] = old_root

        self.assertEqual(metrics["total"], 2)
        self.assertEqual(metrics["task_success_rate"], 50.0)
        self.assertEqual(metrics["verified_outcome_rate"], 50.0)
        self.assertEqual(metrics["target_not_visible_rate"], 50.0)
        self.assertEqual(metrics["parse_failure_rate"], 50.0)
        self.assertEqual(metrics["mean_vlm_latency_ms"], 100.0)


if __name__ == "__main__":
    unittest.main()
