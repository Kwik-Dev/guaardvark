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


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
