"""Platform-agnostic general poster + grounded-eye login preflight (no browser)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.services.social_outreach import general_poster as gp


def _el(tag="div", text="", element_type=""):
    return SimpleNamespace(tag=tag, text=text, element_type=element_type)


def _snap(elements, success=True):
    return SimpleNamespace(success=success, elements=elements)


class TestPreflight:
    def test_login_cta_without_composer_is_logged_out(self):
        snap = _snap([_el(tag="a", text="Log in"), _el(tag="button", text="Sign up")])
        with patch.object(gp, "DOMMetadataExtractor", create=True):
            with patch(
                "backend.services.dom_metadata_extractor.DOMMetadataExtractor.get_instance"
            ) as gi:
                gi.return_value.extract.return_value = snap
                ok, reason = gp._preflight_logged_in("x")
        assert ok is False
        assert reason == "logged_out:x"

    def test_composer_present_passes_even_with_login_word(self):
        snap = _snap([
            _el(tag="textarea", text="Post your reply", element_type="composer"),
            _el(tag="a", text="Log in"),  # header link — composer still present
        ])
        with patch(
            "backend.services.dom_metadata_extractor.DOMMetadataExtractor.get_instance"
        ) as gi:
            gi.return_value.extract.return_value = snap
            ok, reason = gp._preflight_logged_in("x")
        assert ok is True
        assert reason == "ok"

    def test_no_dom_proceeds(self):
        with patch(
            "backend.services.dom_metadata_extractor.DOMMetadataExtractor.get_instance"
        ) as gi:
            gi.return_value.extract.side_effect = RuntimeError("no cdp")
            ok, reason = gp._preflight_logged_in("facebook")
        assert ok is True
        assert "preflight_skipped" in reason


class TestPostViaAgentLoop:
    def _patch_env(self, service):
        """Patch display/screen/service so the loop runs without a browser."""
        return [
            patch("backend.services.agent_control_service.get_agent_control_service",
                  return_value=service),
            patch("backend.utils.agent_display_utils.start_agent_display_if_needed",
                  return_value=True),
            patch("backend.services.local_screen_backend.LocalScreenBackend",
                  return_value=MagicMock()),
            patch("backend.services.social_outreach.reddit_outreach.SERVO_SETTLE_SECONDS", 0),
            patch.object(gp, "_preflight_logged_in", return_value=(True, "ok")),
            patch.object(gp, "_human_pause", return_value=None),
        ]

    def test_empty_text_rejected(self):
        ok, reason = gp.post_via_agent_loop("x", "https://x.com/i/status", "   ")
        assert ok is False
        assert reason == "empty_text"

    def test_busy_agent_rejected(self):
        service = MagicMock()
        service.is_active = True
        with patch("backend.services.agent_control_service.get_agent_control_service",
                   return_value=service):
            ok, reason = gp.post_via_agent_loop("x", "https://x.com/i", "hello world")
        assert ok is False
        assert reason == "agent_busy"

    def test_happy_path_types_text_and_submits(self):
        service = MagicMock()
        service.is_active = False
        service.execute_task.return_value = SimpleNamespace(success=True, reason="ok")
        screen = MagicMock()

        patches = self._patch_env(service)
        # Swap the LocalScreenBackend mock for our screen so we can assert type_text.
        patches[2] = patch(
            "backend.services.local_screen_backend.LocalScreenBackend", return_value=screen
        )
        for p in patches:
            p.start()
        try:
            ok, reason = gp.post_via_agent_loop(
                "facebook", "https://facebook.com/post/1", "Check out our new video!"
            )
        finally:
            for p in patches:
                p.stop()

        assert ok is True and reason == "ok"
        # The user text is typed directly, never interpolated into an LLM task.
        screen.type_text.assert_called_once_with("Check out our new video!")
        # navigate + focus composer + submit = at least 3 loop calls
        assert service.execute_task.call_count >= 3

    def test_navigate_failure_aborts_before_typing(self):
        service = MagicMock()
        service.is_active = False
        service.execute_task.return_value = SimpleNamespace(success=False, reason="dead")
        screen = MagicMock()
        patches = self._patch_env(service)
        patches[2] = patch(
            "backend.services.local_screen_backend.LocalScreenBackend", return_value=screen
        )
        for p in patches:
            p.start()
        try:
            ok, reason = gp.post_via_agent_loop("x", "https://x.com/i", "hello world")
        finally:
            for p in patches:
                p.stop()
        assert ok is False
        assert "navigate_failed" in reason
        screen.type_text.assert_not_called()
