#!/usr/bin/env python3
"""
End-to-end smoke test for Agent Vision Control.
Mocks the screen and vision model to verify the full loop works.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from PIL import Image


class MockScreen:
    """Mock ScreenInterface that returns predictable data."""

    def __init__(self):
        self.actions = []  # Track all actions performed
        self._capture_count = 0
        self._shade = 200

    def capture(self):
        self._capture_count += 1
        img = Image.new("RGB", (1920, 1080), color=(self._shade,) * 3)
        return img, (500, 300)

    def click(self, x, y, button="left", clicks=1):
        self.actions.append(("click", x, y, button))
        # A screen that reacts: the servo's pixel-change check after a click
        # is real, so a click must change what the next capture shows.
        self._shade = 120
        return {"success": True}

    def move(self, x, y):
        self.actions.append(("move", x, y))
        return {"success": True}

    def type_text(self, text, interval=0.05):
        self.actions.append(("type", text))
        return {"success": True}

    def hotkey(self, *keys):
        self.actions.append(("hotkey", list(keys)))
        return {"success": True}

    def scroll(self, x, y, amount=-3):
        self.actions.append(("scroll", x, y, amount))
        return {"success": True}

    def screen_size(self):
        return (1920, 1080)

    def cursor_position(self):
        return (500, 300)


class TestAgentControlE2E(unittest.TestCase):

    def setUp(self):
        # Brain and eye are resolved from Ollama's capability report now; with
        # requests mocked there is no report, so every test here declares the
        # pair the suite has always assumed: a text brain deciding, moondream
        # looking.
        from backend.services.agent_control_service import AgentControlService, BrainEye
        p1 = patch.object(AgentControlService, "resolve_brain_eye",
                          staticmethod(lambda active="", screen_size=None: BrainEye(
                              "llama3:8b", "moondream", False, "sibling_vlm", "test fixture")))
        p2 = patch("backend.services.agent_control_service._eye_accuracy_px", lambda *a: None)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    @patch("backend.utils.vision_analyzer.requests.post")
    @patch("backend.utils.vision_analyzer.requests.get")
    def test_full_loop_click_then_done(self, mock_get, mock_post):
        """Agent sees screen, clicks a button, then reports done."""
        from backend.services.agent_control_service import AgentControlService, AgentControlConfig

        # Mock model list for _get_decision_model
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {"models": [{"name": "llama3:8b"}, {"name": "moondream"}]}
        mock_get.return_value = mock_get_resp

        # Scripted /api/chat turns, in the order the split loop makes them:
        # eye describes, brain decides, eye anchors the click for the servo
        # (the pixel-change check verifies it, no extra turn), eye describes
        # again, brain reports done with proof, eye confirms the proof.
        call_count = [0]
        responses = [
            # Iteration 1:
            "Browser showing Twitter homepage. Tweet button -> D4. Address bar -> D1.",  # vision: scene
            '{"action": "click", "target_cell": "D4", "target_description": "Tweet button", "reasoning": "Click tweet"}',  # text: decision
            '[{"box_2d": [380, 420, 440, 520], "label": "Tweet button"}]',  # vision: servo anchor
            # Iteration 2:
            "Tweet compose dialog is open. Text field -> D4. Post button -> F4.",  # vision: scene
            '{"action": "done", "reasoning": "Tweet dialog opened, task complete", '
            '"success_proof": "Tweet compose dialog with a Post button"}',  # text: decision
            "yes",  # vision: the eye confirms the proof is on screen
        ]

        def make_response(*args, **kwargs):
            url = args[0] if args else kwargs.get("url", "")
            resp = MagicMock()
            resp.status_code = 200
            if not str(url).endswith("/api/chat"):
                # Capability lookups (/api/show) and the keep-alive ping
                # (/api/generate) share requests.post; they must not eat a
                # scripted chat turn.
                resp.json.return_value = {}
                return resp
            idx = min(call_count[0], len(responses) - 1)
            call_count[0] += 1
            resp.json.return_value = {"message": {"content": responses[idx]}}
            return resp

        mock_post.side_effect = make_response

        service = AgentControlService()
        service.config = AgentControlConfig(
            max_iterations=10,
            verify_actions=True,
            vision_model="moondream",
        )

        screen = MockScreen()
        result = service.execute_task("Click the Tweet button", screen)

        self.assertTrue(result.success)
        self.assertEqual(result.reason, "completed")
        self.assertTrue(len(result.steps) > 0)
        click_actions = [a for a in screen.actions if a[0] == "click"]
        self.assertTrue(len(click_actions) > 0)

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_kill_switch_stops_loop(self, mock_post):
        """Kill switch stops the agent mid-task."""
        from backend.services.agent_control_service import AgentControlService, AgentControlConfig
        import threading

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "A blank screen with nothing on it."}
        }
        mock_post.return_value = mock_response

        service = AgentControlService()
        service.config = AgentControlConfig(max_iterations=100)

        # Kill after brief delay
        def kill_later():
            import time
            time.sleep(0.5)
            service.kill()

        screen = MockScreen()
        threading.Thread(target=kill_later, daemon=True).start()

        result = service.execute_task("Do something forever", screen)
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "killed")

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_max_failures_triggers_kill(self, mock_post):
        """Max consecutive failures triggers automatic kill switch."""
        from backend.services.agent_control_service import AgentControlService, AgentControlConfig
        import requests

        # Simulate Ollama connection failures
        mock_post.side_effect = requests.ConnectionError("Ollama down")

        service = AgentControlService()
        service.config = AgentControlConfig(
            max_iterations=20,
            max_consecutive_failures=3,
        )

        screen = MockScreen()
        result = service.execute_task("Try something", screen)
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
