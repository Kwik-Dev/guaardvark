# backend/tests/test_vision_analyzer.py
#!/usr/bin/env python3

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"


class TestVisionAnalyzer(unittest.TestCase):

    def test_encode_image_returns_base64_string(self):
        from backend.utils.vision_analyzer import VisionAnalyzer
        from PIL import Image
        analyzer = VisionAnalyzer()
        img = Image.new("RGB", (100, 100), color=(255, 0, 0))
        b64 = analyzer.encode_image(img)
        self.assertIsInstance(b64, str)
        self.assertTrue(len(b64) > 0)

    def test_encode_image_respects_max_width(self):
        from backend.utils.vision_analyzer import VisionAnalyzer
        from PIL import Image
        analyzer = VisionAnalyzer(max_width=256)
        img = Image.new("RGB", (1920, 1080), color=(0, 0, 0))
        b64 = analyzer.encode_image(img)
        # Decode and check dimensions
        import base64
        from io import BytesIO
        decoded = Image.open(BytesIO(base64.b64decode(b64)))
        self.assertEqual(decoded.width, 256)

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_calls_ollama_correctly(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        from PIL import Image

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "A red square on white background"}
        }
        mock_post.return_value = mock_response

        analyzer = VisionAnalyzer(ollama_url="http://localhost:11434", default_model="gemma4:e4b")
        img = Image.new("RGB", (100, 100), color=(255, 0, 0))
        result = analyzer.analyze(img, prompt="What do you see?")

        self.assertEqual(result.description, "A red square on white background")
        self.assertEqual(result.model_used, "gemma4:e4b")

        # Verify Ollama was called with correct structure
        call_args = mock_post.call_args
        self.assertEqual(call_args[0][0], "http://localhost:11434/api/chat")
        payload = call_args[1]["json"]
        self.assertEqual(payload["model"], "gemma4:e4b")
        self.assertEqual(len(payload["messages"]), 1)
        self.assertIn("images", payload["messages"][0])

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_with_custom_model(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        from PIL import Image

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "Detailed description"}
        }
        mock_post.return_value = mock_response

        analyzer = VisionAnalyzer()
        img = Image.new("RGB", (100, 100))
        result = analyzer.analyze(img, prompt="Describe", model="llava:13b")

        self.assertEqual(result.model_used, "llava:13b")
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["model"], "llava:13b")

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_handles_timeout(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer, VisionResult
        from PIL import Image
        import requests

        mock_post.side_effect = requests.Timeout("Connection timed out")

        analyzer = VisionAnalyzer()
        img = Image.new("RGB", (100, 100))
        result = analyzer.analyze(img, prompt="What is this?")

        self.assertFalse(result.success)
        self.assertIn("timed out", result.error.lower())

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_analyze_handles_connection_error(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        from PIL import Image
        import requests

        mock_post.side_effect = requests.ConnectionError("Ollama not running")

        analyzer = VisionAnalyzer()
        img = Image.new("RGB", (100, 100))
        result = analyzer.analyze(img, prompt="Describe")

        self.assertFalse(result.success)
        self.assertIn("connection", result.error.lower())

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_text_query_calls_ollama_without_images(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": '{"action": "click", "target_cell": "D4"}'}
        }
        mock_post.return_value = mock_response

        analyzer = VisionAnalyzer()
        result = analyzer.text_query("Decide next action", model="llama3:8b")

        self.assertTrue(result.success)
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["model"], "llama3:8b")
        # text_query should NOT include images
        self.assertNotIn("images", payload["messages"][0])

    @patch("backend.utils.vision_analyzer.requests.get")
    def test_get_decision_model_prefers_text_models(self, mock_get):
        from backend.utils.vision_analyzer import VisionAnalyzer

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "moondream"},
                {"name": "llava:7b"},
                {"name": "gemma4:e4b"},
                {"name": "llama3:8b"},
            ]
        }
        mock_get.return_value = mock_response

        analyzer = VisionAnalyzer()
        model = analyzer._get_decision_model()
        # The legacy fallback picks a TEXT model. gemma4 was removed from its
        # preference list on 2026-09-22: a vision model quietly becoming the
        # decider for a user who chose a text model was the fault being fixed.
        self.assertEqual(model, "llama3:8b")
        self.assertNotIn("gemma4", model)

    @patch("backend.services.model_capabilities.capabilities_for")
    @patch("backend.utils.vision_analyzer.requests.get")
    def test_get_decision_model_skips_models_that_cannot_write_text(self, mock_get, mock_caps):
        from backend.utils.vision_analyzer import VisionAnalyzer

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "nimble:9b-q4_K_M"},
                {"name": "nomic-embed-text:latest"},
                {"name": "qwen3:14b"},
            ]
        }
        mock_get.return_value = mock_response
        caps = {
            "nimble:9b-q4_K_M": dict(exists=True, completion=False, embedding=False),
            "nomic-embed-text:latest": dict(exists=True, completion=False, embedding=True),
            "qwen3:14b": dict(exists=True, completion=True, embedding=False),
        }
        mock_caps.side_effect = lambda name, with_vision=False: MagicMock(**caps[name])

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GUAARDVARK_DECISION_MODEL", None)
            model = VisionAnalyzer()._get_decision_model()
        # A decision-only model (capability "decision", no "completion") cannot
        # answer a text query, and an embedding model cannot either.
        self.assertEqual(model, "qwen3:14b")


# /api/show capability lists, as Ollama reports them for these tags.
_GEMMA4 = ("completion", "vision", "audio", "tools", "thinking")
_STOCK_CLONE = {
    "gemma4:e2b": _GEMMA4,
    "nomic-embed-text:latest": ("embedding",),
}
# Decision models listed first, as /api/tags lists the newest downloads first.
_DECISION_MODELS_FIRST = {
    "nimble:9b-q4_K_M": ("decision",),
    "tev1:4b": ("decision",),
    "tev1:0.8b": ("decision",),
    "qwen3-embedding:4b-q4_K_M": ("tools", "embedding"),
    "moondream:latest": ("completion", "vision"),
    "qwen3.5:9b": ("completion", "vision", "tools", "thinking"),
    "qwen3:14b": ("completion", "tools", "thinking"),
    "embeddinggemma:latest": ("embedding",),
    "gemma4:e4b": _GEMMA4,
    "gemma4:12b": _GEMMA4,
    "nomic-embed-text:latest": ("embedding",),
    "gemma4:e2b": _GEMMA4,
}
_CANNOT_REPLY = {name for table in (_STOCK_CLONE, _DECISION_MODELS_FIRST)
                 for name, caps in table.items() if "completion" not in caps}


class TestLegacyDecisionModel(unittest.TestCase):
    """VisionAnalyzer._get_decision_model, the text model used when the caller
    (apprentice replay, Film Crew curator) names none."""

    def setUp(self):
        self._tags = []
        self._caps = {}
        self._described = True
        self._saved = None
        patches = [
            patch("backend.utils.vision_analyzer.requests.get", side_effect=self._fake_tags),
            patch("backend.services.model_capabilities.capabilities_for", side_effect=self._fake_caps),
            patch("backend.config._read_saved_model_name", side_effect=lambda: self._saved),
            patch.dict(os.environ, {}, clear=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("GUAARDVARK_DECISION_MODEL", None)

    def _fake_tags(self, url, timeout=None):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"models": [{"name": n} for n in self._tags]}
        return response

    def _fake_caps(self, name, with_vision=False):
        caps = tuple(self._caps.get(name, ())) if self._described else ()
        return MagicMock(exists=self._described and name in self._caps, capabilities=caps,
                         completion="completion" in caps, embedding="embedding" in caps)

    def _pick(self, table, order=None, saved=None, described=True):
        from backend.utils.vision_analyzer import VisionAnalyzer
        self._caps = dict(table)
        self._tags = list(order if order is not None else table)
        self._saved = saved
        self._described = described
        return VisionAnalyzer(default_model="moondream:latest")._pick_decision_model()

    def test_stock_clone_picks_the_chat_model_not_the_embedding_model(self):
        for order in (list(_STOCK_CLONE), list(reversed(_STOCK_CLONE))):
            for saved in (None, "gemma4:e2b"):
                with self.subTest(order=order, saved=saved):
                    model, why = self._pick(_STOCK_CLONE, order=order, saved=saved)
                    self.assertEqual(model, "gemma4:e2b", why)

    def test_stock_clone_when_ollama_cannot_describe_the_models(self):
        model, why = self._pick(_STOCK_CLONE, order=["nomic-embed-text:latest", "gemma4:e2b"],
                                described=False)
        self.assertEqual(model, "gemma4:e2b", why)

    def test_decision_and_embedding_models_are_never_picked(self):
        names = list(_DECISION_MODELS_FIRST)
        for shift in range(len(names)):
            order = names[shift:] + names[:shift]
            with self.subTest(first=order[0]):
                model, why = self._pick(_DECISION_MODELS_FIRST, order=order)
                self.assertIsNotNone(model, why)
                self.assertNotIn(model, _CANNOT_REPLY)
                self.assertIn("completion", _DECISION_MODELS_FIRST[model])

    def test_an_override_that_cannot_reply_is_passed_over(self):
        os.environ["GUAARDVARK_DECISION_MODEL"] = "nimble:9b-q4_K_M"
        table = {"nimble:9b-q4_K_M": ("decision",), **_STOCK_CLONE}
        model, why = self._pick(table)
        self.assertEqual(model, "gemma4:e2b", why)

    def test_an_override_that_can_reply_wins(self):
        os.environ["GUAARDVARK_DECISION_MODEL"] = "gemma4:12b"
        model, _ = self._pick(_DECISION_MODELS_FIRST)
        self.assertEqual(model, "gemma4:12b")

    def test_the_saved_chat_model_is_preferred_among_vision_models(self):
        table = {"moondream:latest": ("completion", "vision"), "gemma4:e2b": _GEMMA4, "gemma4:12b": _GEMMA4}
        self.assertEqual(self._pick(table, saved="gemma4:12b")[0], "gemma4:12b")
        # With no saved choice a full chat model beats a captioner listed first.
        self.assertEqual(self._pick(table, saved=None)[0], "gemma4:e2b")

    @patch("backend.utils.vision_analyzer.requests.post")
    def test_no_model_that_can_reply_gives_none_with_a_reason(self, mock_post):
        from backend.utils.vision_analyzer import VisionAnalyzer
        table = {"nomic-embed-text:latest": ("embedding",), "nimble:9b-q4_K_M": ("decision",)}

        model, why = self._pick(table)
        self.assertIsNone(model)
        self.assertIn("install a chat model", why)

        result = VisionAnalyzer(default_model="moondream:latest").text_query("Is this frame usable?")
        self.assertFalse(result.success)
        self.assertEqual(result.error, why)
        mock_post.assert_not_called()

    def test_ollama_unreachable_gives_none_with_a_reason(self):
        import requests
        from backend.utils.vision_analyzer import VisionAnalyzer
        with patch("backend.utils.vision_analyzer.requests.get",
                   side_effect=requests.ConnectionError("refused")):
            model, why = VisionAnalyzer(default_model="moondream:latest")._pick_decision_model()
        self.assertIsNone(model)
        self.assertIn("could not reach Ollama", why)


if __name__ == "__main__":
    unittest.main()
