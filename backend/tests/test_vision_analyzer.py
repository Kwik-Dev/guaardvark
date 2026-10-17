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
        nothing_loaded = MagicMock(status_code=200)
        nothing_loaded.json.return_value = {"models": []}
        mock_get.side_effect = lambda url, timeout=None: (
            nothing_loaded if url.endswith("/api/ps") else mock_response)

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
        nothing_loaded = MagicMock(status_code=200)
        nothing_loaded.json.return_value = {"models": []}
        mock_get.side_effect = lambda url, timeout=None: (
            nothing_loaded if url.endswith("/api/ps") else mock_response)
        none = dict(vision=False, tools=False, decision=False)
        caps = {
            "nimble:9b-q4_K_M": dict(none, exists=True, capabilities=("decision",), completion=False,
                                     embedding=False, decision=True),
            "nomic-embed-text:latest": dict(none, exists=True, capabilities=("embedding",), completion=False,
                                            embedding=True),
            "qwen3:14b": dict(none, exists=True, capabilities=("completion", "tools"), completion=True,
                              embedding=False, tools=True),
        }
        mock_caps.side_effect = lambda name, with_vision=True: MagicMock(**caps[name])

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
        self._loaded = []
        self._digests = {}
        patches = [
            patch("backend.utils.vision_analyzer.requests.get", side_effect=self._fake_get),
            patch("backend.services.model_capabilities.capabilities_for", side_effect=self._fake_caps),
            patch("backend.config._read_saved_model_name", side_effect=lambda: self._saved),
            patch.dict(os.environ, {}, clear=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("GUAARDVARK_DECISION_MODEL", None)

    def _fake_get(self, url, timeout=None):
        """/api/ps lists the loaded models, /api/tags the installed ones."""
        response = MagicMock()
        response.status_code = 200
        names = self._loaded if url.endswith("/api/ps") else self._tags
        response.json.return_value = {"models": [{"name": n, "digest": self._digests.get(n, "")}
                                                 for n in names]}
        return response

    def _fake_caps(self, name, with_vision=True):
        """The capability record; vision is the resolver's answer, a name guess
        when Ollama cannot describe the model."""
        from backend.services.model_capability_data import name_looks_vision
        described = self._described and name in self._caps
        caps = tuple(self._caps.get(name, ())) if described else ()
        return MagicMock(exists=described, capabilities=caps,
                         completion="completion" in caps, embedding="embedding" in caps,
                         decision="decision" in caps, tools="tools" in caps,
                         vision=("vision" in caps) if described else name_looks_vision(name))

    def _pick(self, table, order=None, saved=None, described=True, loaded=()):
        from backend.utils.vision_analyzer import VisionAnalyzer
        self._caps = dict(table)
        self._tags = list(order if order is not None else table)
        self._saved = saved
        self._described = described
        self._loaded = list(loaded)
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

    # ---- by capability, preferring the loaded chat model ----

    _BOX = {
        # listed in this order by /api/tags: a vision chat model before the text-only ones
        "qwen3.8:latest": ("completion", "vision", "tools", "thinking"),
        "nimble:9b-q4_K_M": ("decision",),
        "gemma4:e2b": _GEMMA4,
        "qwen3-14b-16k:latest": ("completion", "tools", "thinking"),
        "qwen3:14b": ("completion", "tools", "thinking"),
        "nomic-embed-text:latest": ("embedding",),
    }

    def test_the_loaded_chat_model_is_picked_when_it_can_reply(self):
        model, why = self._pick(self._BOX, saved="gemma4:e2b", loaded=["gemma4:e2b"])
        self.assertEqual(model, "gemma4:e2b")
        self.assertIn("already loaded", why)

    def test_with_nothing_loaded_a_text_only_model_is_picked_not_a_vision_one(self):
        model, why = self._pick(self._BOX, saved="gemma4:e2b")
        self.assertEqual(model, "qwen3-14b-16k:latest", why)

    def test_a_loaded_text_only_model_beats_loading_another(self):
        model, why = self._pick(self._BOX, saved="gemma4:e2b", loaded=["qwen3:14b"])
        self.assertEqual(model, "qwen3:14b")
        self.assertEqual(why, "a text-only model already loaded")

    def test_the_loaded_chat_model_comes_before_another_loaded_model(self):
        model, _ = self._pick(self._BOX, saved="gemma4:e2b", loaded=["qwen3:14b", "gemma4:e2b"])
        self.assertEqual(model, "gemma4:e2b")

    def test_a_loaded_vision_model_that_is_not_the_chat_model_is_not_preferred(self):
        model, _ = self._pick(self._BOX, saved="gemma4:e2b", loaded=["qwen3.8:latest"])
        self.assertEqual(model, "qwen3-14b-16k:latest")

    def test_the_chat_model_loaded_under_another_name_for_its_build(self):
        alias = "llamacpp:" + "2cf98652fdc6" + "a" * 52
        table = {**self._BOX, alias: _GEMMA4}
        self._digests = {"gemma4:e2b": "2cf98652fdc6" + "a" * 52, alias: "2cf98652fdc6" + "a" * 52}
        model, why = self._pick(table, saved=alias, loaded=["gemma4:e2b"])
        self.assertEqual(model, "gemma4:e2b")
        self.assertIn("already loaded", why)

    def test_a_loaded_chat_model_that_cannot_reply_is_passed_over(self):
        model, _ = self._pick(self._BOX, saved="nimble:9b-q4_K_M", loaded=["nimble:9b-q4_K_M"])
        self.assertEqual(model, "qwen3-14b-16k:latest")

    def test_a_vision_model_outside_the_old_name_list_never_beats_a_text_only_one(self):
        table = {"pixtral-next:12b": ("completion", "vision", "tools"), "qwen3:14b": ("completion", "tools")}
        for order in (list(table), list(reversed(table))):
            with self.subTest(order=order):
                self.assertEqual(self._pick(table, order=order)[0], "qwen3:14b")

    def test_a_vision_named_model_that_does_not_see_is_text_only(self):
        """A gemma4-named build without a vision tower is a text model."""
        table = {"moondream:latest": ("completion", "vision"),
                 "someone/gemma4-26b-text-only:latest": ("completion", "tools", "thinking")}
        model, why = self._pick(table)
        self.assertEqual(model, "someone/gemma4-26b-text-only:latest")
        self.assertEqual(why, "first installed text-only model")

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
