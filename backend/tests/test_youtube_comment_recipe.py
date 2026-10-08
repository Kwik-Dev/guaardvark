"""youtube_comment recipe — fires only on explicit text, submits via Ctrl+Enter."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

RECIPES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "agent", "recipes.json",
)


def _recipe():
    with open(RECIPES) as f:
        return json.load(f)["youtube_comment"]


def _match(task):
    for t in _recipe()["triggers"]:
        m = re.search(t, task, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


class TestTriggers:
    def test_explicit_quoted_text_matches_and_captures(self):
        assert _match("leave a comment 'Check out guaardvark.com' on this video") == "Check out guaardvark.com"
        assert _match('write a comment "First!"') == "First!"

    def test_saying_form_captures_text(self):
        assert _match("post a comment saying Learn more at guaardvark.com") == "Learn more at guaardvark.com"

    def test_freeform_intent_does_not_match(self):
        # Instruction, not literal text — must fall through so the loop composes it,
        # never type the instruction verbatim as the comment.
        assert _match("leave a comment to let people know that they can learn more at guaardvark.com") is None

    def test_read_comment_is_not_a_post(self):
        assert _match('read the comment "hello" to me') is None


class TestSteps:
    def test_submits_via_ctrl_enter_not_vision_click(self):
        steps = _recipe()["steps"]
        submit = [s for s in steps if s.get("action") == "hotkey"
                  and [k.lower() for k in s.get("keys", [])] == ["ctrl", "return"]]
        assert submit, "recipe must submit via Ctrl+Enter (avoids the Comment-button drift)"

    def test_types_captured_text_placeholder(self):
        steps = _recipe()["steps"]
        assert any(s.get("action") == "type" and "{1}" in s.get("text", "") for s in steps)

    def test_verifies_composer_expanded_before_typing(self):
        steps = _recipe()["steps"]
        actions = [s.get("action") for s in steps]
        # a wait_until_visible (Cancel button) must precede the type step
        type_idx = actions.index("type")
        assert "wait_until_visible" in actions[:type_idx]

    def test_precondition_firefox_running(self):
        assert "firefox_running" in _recipe().get("preconditions", [])


class TestPageHostCheck:
    """The comment is public, so the recipe runs only on a youtube.com page."""

    TASK = "post a comment saying Learn more at guaardvark.com"

    def _service(self, page_url, preconditions_pass=True):
        from unittest.mock import MagicMock
        from backend.services.agent_control_service import AgentControlService

        svc = AgentControlService.__new__(AgentControlService)
        svc._load_recipes = lambda: {
            "youtube_comment": {
                "description": "Comment on the open YouTube video",
                "triggers": [r"^post a comment saying (.+)$"],
                "preconditions": ["firefox_running"],
                "steps": [{"action": "type", "text": "{1}"}],
            },
        }
        svc._recipe_disabled = lambda name: False
        svc._preconditions_pass = lambda recipe, screen: preconditions_pass
        svc._current_page_url = lambda: page_url
        svc._execute_recipe = MagicMock(return_value="ran")
        return svc

    def _refused(self, result):
        from backend.services.agent_control_service import RECIPE_REFUSED
        return (result is not None and result != "ran" and result.success is False
                and result.reason.startswith(RECIPE_REFUSED))

    def test_other_site_is_refused_with_the_reason(self):
        from unittest.mock import MagicMock
        svc = self._service("https://example.com/some/post")
        result = svc._try_recipe(self.TASK, MagicMock())
        assert self._refused(result)
        assert "youtube.com" in result.reason and "example.com" in result.reason
        svc._execute_recipe.assert_not_called()

    def test_lookalike_host_is_refused(self):
        from unittest.mock import MagicMock
        svc = self._service("https://youtube.com.example.net/watch?v=abc")
        assert self._refused(svc._try_recipe(self.TASK, MagicMock()))
        svc._execute_recipe.assert_not_called()

    def test_unreadable_page_is_refused(self):
        from unittest.mock import MagicMock
        svc = self._service("")
        result = svc._try_recipe(self.TASK, MagicMock())
        assert self._refused(result)
        assert "could not be read" in result.reason
        svc._execute_recipe.assert_not_called()

    def test_refused_even_when_other_gates_would_defer_to_the_loop(self):
        # Deferring hands "post a comment saying ..." to the loop, which would
        # post it on whatever page is open.
        from unittest.mock import MagicMock
        svc = self._service("https://example.com/", preconditions_pass=False)
        assert self._refused(svc._try_recipe(self.TASK, MagicMock()))

    def test_youtube_watch_page_runs(self):
        from unittest.mock import MagicMock
        for url in ("https://www.youtube.com/watch?v=abc", "https://m.youtube.com/watch?v=abc"):
            svc = self._service(url)
            assert svc._try_recipe(self.TASK, MagicMock()) == "ran"
            svc._execute_recipe.assert_called_once()

    def test_recipes_without_a_site_are_not_checked(self):
        from backend.services.agent_control_service import AgentControlService
        svc = AgentControlService.__new__(AgentControlService)
        svc._current_page_url = lambda: ""
        assert svc._page_host_refusal("open_firefox") == ""

    def test_page_address_comes_from_the_browser_snapshot(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from backend.services.agent_control_service import AgentControlService
        svc = AgentControlService.__new__(AgentControlService)
        target = "backend.services.dom_metadata_extractor.DOMMetadataExtractor.get_instance"
        with patch(target) as gi:
            gi.return_value.extract.return_value = SimpleNamespace(
                success=True, url="https://www.youtube.com/watch?v=abc")
            assert svc._current_page_url() == "https://www.youtube.com/watch?v=abc"
            gi.return_value.extract.return_value = SimpleNamespace(
                success=False, url="", error="BiDi connect failed")
            assert svc._current_page_url() == ""


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
