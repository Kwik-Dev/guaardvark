"""Platform-agnostic general poster + grounded-eye login preflight (no browser)."""
import json
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


class _FakeSocket:
    """BiDi socket that answers each request with the next queued reply."""

    def __init__(self, replies):
        self.replies = [json.dumps(r) for r in replies]
        self.sent = []
        self.closed = False

    def send(self, message):
        self.sent.append(json.loads(message))

    def recv(self):
        return self.replies.pop(0)

    def close(self):
        self.closed = True


class TestBidiEvaluateJson:
    """The shared page reader the posters use after a submit."""

    OPEN = [{"type": "success"},
            {"type": "success", "result": {"contexts": [{"context": "tab-1"}]}}]

    def _evaluate(self, replies, expression="(() => '{}')()"):
        from backend.services.social_outreach import reddit_outreach
        sock = _FakeSocket(replies)
        with patch("websocket.create_connection", return_value=sock):
            result = reddit_outreach.bidi_evaluate_json(expression)
        return result, sock

    def test_returns_the_parsed_object_and_ends_the_session(self):
        value = json.dumps({"text_on_page": True})
        (data, why), sock = self._evaluate(
            self.OPEN + [{"type": "success", "result": {"result": {"type": "string", "value": value}}}])
        assert (data, why) == ({"text_on_page": True}, "")
        evaluate = sock.sent[2]
        assert evaluate["method"] == "script.evaluate"
        assert evaluate["params"]["target"] == {"context": "tab-1"}
        assert sock.sent[-1]["method"] == "session.end" and sock.closed

    def test_evaluate_error_is_reported_and_the_session_still_ends(self):
        (data, why), sock = self._evaluate(
            self.OPEN + [{"type": "error", "message": "no such frame"}])
        assert data is None and "no such frame" in why
        assert sock.sent[-1]["method"] == "session.end"

    def test_unreachable_browser_is_reported(self):
        from backend.services.social_outreach import reddit_outreach
        with patch("websocket.create_connection", side_effect=OSError("refused")):
            data, why = reddit_outreach.bidi_evaluate_json("1")
        assert data is None and "connect failed" in why


# What the post-submit page check reports for a page showing the text in the
# feed with an empty composer.
PAGE_POSTED = {"text_on_page": True, "composers": 1, "composers_with_text": 0,
               "url": "https://x.com/a/status/1"}


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
            patch.object(gp, "_still_on_target", return_value=(True, "")),
            patch("backend.services.social_outreach.reddit_outreach.bidi_reachable",
                  return_value=(True, "")),
            patch("backend.services.social_outreach.reddit_outreach.bidi_evaluate_json",
                  return_value=(PAGE_POSTED, "")),
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

    def _post_with_page(self, page, text="Check out our new video!"):
        """Run the loop with every agent task succeeding and the page check
        answering ``page``; return the result and the expressions evaluated."""
        service = MagicMock()
        service.is_active = False
        service.execute_task.return_value = SimpleNamespace(success=True, reason="ok")
        seen = []

        def evaluate(expression):
            seen.append(expression)
            return page

        patches = self._patch_env(service)
        patches[-1] = patch(
            "backend.services.social_outreach.reddit_outreach.bidi_evaluate_json",
            side_effect=evaluate,
        )
        for p in patches:
            p.start()
        try:
            result = gp.post_via_agent_loop("x", "https://x.com/a/status/1", text)
        finally:
            for p in patches:
                p.stop()
        return result, seen

    def test_text_only_in_the_composer_is_unverified(self):
        page = {"text_on_page": False, "composers": 1, "composers_with_text": 1,
                "url": "https://x.com/a/status/1"}
        (ok, reason), _ = self._post_with_page((page, ""))
        assert ok is False
        assert reason.startswith("submit_unverified")
        assert "text_on_page=False" in reason and "composers_with_text=1" in reason

    def test_text_in_the_feed_with_an_empty_composer_is_posted(self):
        (ok, reason), seen = self._post_with_page((PAGE_POSTED, ""))
        assert (ok, reason) == (True, "ok")
        # The page is searched for the start of the posted text.
        assert '"Check out our new video!"' in seen[0]

    def test_text_in_the_feed_but_left_in_a_composer_is_unverified(self):
        page = dict(PAGE_POSTED, composers_with_text=1)
        (ok, reason), _ = self._post_with_page((page, ""))
        assert ok is False
        assert reason.startswith("submit_unverified")

    def test_unreadable_page_is_unverified(self):
        (ok, reason), _ = self._post_with_page((None, "connect failed: refused"))
        assert ok is False
        assert reason.startswith("submit_unverified") and "connect failed" in reason

    def test_the_page_is_searched_for_the_first_60_characters(self):
        text = "a" * 59 + "bcdefgh"
        (ok, _), seen = self._post_with_page((PAGE_POSTED, ""), text=text)
        assert ok is True
        assert '"' + "a" * 59 + 'b"' in seen[0]
        assert "a" * 59 + "bc" not in seen[0]


class TestPageCheckBeforePosting:
    """Without the BiDi port a post cannot be confirmed, so none is attempted."""

    def _run(self, reachable):
        tasks = []

        class Service:
            is_active = False

            def execute_task(self, task, screen):
                tasks.append(task)
                return SimpleNamespace(success=True, reason="ok")

        from backend.services.social_outreach import reddit_outreach
        with patch("backend.services.agent_control_service.get_agent_control_service",
                   return_value=Service()), \
             patch("backend.utils.agent_display_utils.start_agent_display_if_needed",
                   return_value=True), \
             patch("backend.services.local_screen_backend.LocalScreenBackend", MagicMock), \
             patch.object(reddit_outreach, "bidi_reachable", return_value=reachable):
            result = gp.post_via_agent_loop("facebook", "https://www.facebook.com/p/1", "hello")
        return result, tasks

    def test_closed_port_refuses_before_navigating(self):
        (ok, reason), tasks = self._run((False, "connect failed: refused"))
        assert ok is False
        assert reason.startswith("page_check_unavailable: nothing was posted")
        assert tasks == []

    def test_open_port_goes_on_to_navigate(self):
        with patch.object(gp, "_preflight_logged_in", return_value=(False, "logged_out:facebook")):
            (ok, reason), tasks = self._run((True, ""))
        assert (ok, reason) == (False, "logged_out:facebook")
        assert tasks == ["navigate to https://www.facebook.com/p/1"]


class TestBidiReachable:
    def test_answers_true_when_the_browser_evaluates(self):
        from backend.services.social_outreach import reddit_outreach
        with patch.object(reddit_outreach, "bidi_evaluate_json", return_value=({"ok": True}, "")):
            assert reddit_outreach.bidi_reachable(wait_s=0) == (True, "")

    def test_gives_up_after_the_wait_with_the_reason(self):
        from backend.services.social_outreach import reddit_outreach
        calls = []

        def closed(expression):
            calls.append(expression)
            return None, "connect failed: refused"

        with patch.object(reddit_outreach, "bidi_evaluate_json", side_effect=closed), \
             patch.object(reddit_outreach.time, "sleep", lambda s: None):
            assert reddit_outreach.bidi_reachable(wait_s=0) == (False, "connect failed: refused")
        assert len(calls) == 1

    def test_a_start_page_that_refuses_scripts_still_counts_as_reachable(self):
        from backend.services.social_outreach import reddit_outreach
        refused = (None, 'evaluate error: System access is required. Start Firefox with "-remote-allow-system-access" to enable it.')
        with patch.object(reddit_outreach, "bidi_evaluate_json", return_value=refused):
            assert reddit_outreach.bidi_reachable(wait_s=0) == (True, "")


class TestStaysOnTarget:
    """Nothing is typed or submitted once the browser has left the approved page."""

    def _run(self, places):
        service = MagicMock()
        service.is_active = False
        service.execute_task.return_value = SimpleNamespace(success=True, reason="ok")
        screen = MagicMock()
        answers = iter(places)
        with patch("backend.services.agent_control_service.get_agent_control_service", return_value=service), \
             patch("backend.utils.agent_display_utils.start_agent_display_if_needed", return_value=True), \
             patch("backend.services.local_screen_backend.LocalScreenBackend", return_value=screen), \
             patch("backend.services.social_outreach.reddit_outreach.SERVO_SETTLE_SECONDS", 0), \
             patch("backend.services.social_outreach.reddit_outreach.bidi_reachable", return_value=(True, "")), \
             patch.object(gp, "_preflight_logged_in", return_value=(True, "ok")), \
             patch.object(gp, "_human_pause", return_value=None), \
             patch.object(gp, "_still_on_target", side_effect=lambda url: next(answers)):
            result = gp.post_via_agent_loop("facebook", "https://www.facebook.com/reel/1", "hello")
        tasks = [c.args[0] for c in service.execute_task.call_args_list]
        return result, screen, tasks

    def test_leaving_before_typing_types_nothing(self):
        (ok, reason), screen, _ = self._run([(False, "https://www.facebook.com/reel/2")])
        assert ok is False
        assert reason.startswith("wrong_page: nothing was posted")
        screen.type_text.assert_not_called()

    def test_leaving_before_submitting_submits_nothing(self):
        (ok, reason), screen, tasks = self._run([(True, ""), (False, "https://www.facebook.com/reel/2")])
        assert ok is False
        assert reason.startswith("wrong_page: nothing was posted")
        screen.type_text.assert_called_once()
        assert not [t for t in tasks if "publishes/submits" in t]

    def test_same_page_ignores_scheme_www_query_and_slash(self):
        assert gp._same_page("https://www.facebook.com/reel/1/?s=x", "http://facebook.com/reel/1")
        assert not gp._same_page("https://www.facebook.com/reel/2", "https://www.facebook.com/reel/1")
        assert not gp._same_page("https://evil.example/reel/1", "https://www.facebook.com/reel/1")

