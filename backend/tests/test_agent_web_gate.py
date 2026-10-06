#!/usr/bin/env python3
"""Web access off has to reach the screen agent's own browser: the managed
user.js block, the agent's navigate refusal, and the outreach posting gate."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"


class TestFirefoxGate(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.user_js = os.path.join(self.dir, "user.js")
        with open(self.user_js, "w") as f:
            f.write('user_pref("browser.startup.page", 1);\n')

    def _read(self):
        with open(self.user_js) as f:
            return f.read()

    def test_blocked_block_refuses_outside_and_keeps_localhost(self):
        from backend.utils import agent_web_gate as g
        g.write_firefox_gate(False, self.dir)
        text = self._read()
        self.assertIn('user_pref("browser.startup.page", 1);', text, "the rest of user.js stays")
        self.assertIn('user_pref("network.proxy.type", 1);', text)
        self.assertIn('user_pref("network.proxy.ssl_port", 9);', text)
        self.assertIn('user_pref("network.proxy.failover_direct", false);', text)
        self.assertIn('user_pref("network.trr.mode", 5);', text)
        self.assertIn('user_pref("network.proxy.no_proxies_on", "localhost, 127.0.0.1, [::1]");', text)
        self.assertIs(g.gate_state(self.dir), False)

    def test_switching_replaces_the_block_instead_of_stacking(self):
        from backend.utils import agent_web_gate as g
        g.write_firefox_gate(False, self.dir)
        g.write_firefox_gate(True, self.dir)
        g.write_firefox_gate(True, self.dir)
        text = self._read()
        self.assertEqual(text.count(g.GATE_BEGIN), 1)
        self.assertNotIn('user_pref("network.proxy.type", 1);', text)
        self.assertIn('user_pref("network.proxy.type", 0);', text)
        self.assertIs(g.gate_state(self.dir), True)

    def test_no_block_reads_as_unknown(self):
        from backend.utils import agent_web_gate as g
        self.assertIsNone(g.gate_state(self.dir))

    def test_apply_fails_closed_without_the_backend(self):
        from backend.utils import agent_web_gate as g
        with patch.object(g, "_setting_from_backend", return_value=None):
            self.assertEqual(g.main(["apply", "--profile", self.dir]), 0)
        self.assertIs(g.gate_state(self.dir), False)
        with patch.object(g, "_setting_from_backend", return_value=True):
            g.main(["apply", "--profile", self.dir])
        self.assertIs(g.gate_state(self.dir), True)

    def test_off_stops_the_task_and_closes_the_browser(self):
        from backend.utils import agent_web_gate as g

        class FakeACS:
            _active = True
            killed = False

            def kill(self):
                self.killed = True

        acs = FakeACS()
        with patch.object(g, "default_profile_dir", return_value=self.dir), \
             patch("backend.services.agent_control_service.get_agent_control_service", return_value=acs), \
             patch.object(g, "close_agent_firefox", return_value=1) as close:
            out = g.enforce(False, "test")
        self.assertTrue(acs.killed)
        self.assertTrue(out["task_stopped"])
        self.assertEqual(out["browser_closed"], 1)
        close.assert_called_once()
        self.assertIs(g.gate_state(self.dir), False)

    def test_on_leaves_a_running_task_alone(self):
        from backend.utils import agent_web_gate as g

        class FakeACS:
            _active = True

            def kill(self):
                raise AssertionError("must not stop a running task when web access goes on")

        with patch.object(g, "default_profile_dir", return_value=self.dir), \
             patch("backend.services.agent_control_service.get_agent_control_service", return_value=FakeACS()), \
             patch.object(g, "close_agent_firefox", return_value=0) as close:
            out = g.enforce(True, "test")
        close.assert_not_called()
        self.assertFalse(out["task_stopped"])


class TestAgentNavigate(unittest.TestCase):

    def test_local_urls(self):
        from backend.services.agent_control_service import AgentControlService as A
        for url in ("file:///home/x/page.html", "http://localhost:5173/chat", "localhost:5000",
                    "http://127.0.0.1:8188/", "about:blank"):
            self.assertTrue(A._is_local_url(url), url)
        for url in ("https://example.com", "youtube.com", "http://192.168.1.20:5000/"):
            self.assertFalse(A._is_local_url(url), url)

    def test_navigate_outside_is_refused_with_web_access_off(self):
        from contextlib import nullcontext
        from backend.services.agent_control_service import AgentControlService, AgentAction
        svc = AgentControlService()
        with patch.object(AgentControlService, "_app_context", lambda self: nullcontext()), \
             patch("backend.utils.settings_utils.web_access_block_reason", return_value="Web access is disabled."):
            why = svc._refusal_for(AgentAction(action_type="navigate", url="https://example.com"))
            self.assertIn("web access is off", why)
            self.assertEqual(svc._refusal_for(AgentAction(action_type="navigate", url="file:///tmp/p.html")), "")
        with patch.object(AgentControlService, "_app_context", lambda self: nullcontext()), \
             patch("backend.utils.settings_utils.web_access_block_reason", return_value=None):
            self.assertEqual(svc._refusal_for(AgentAction(action_type="navigate", url="https://example.com")), "")


if __name__ == "__main__":
    unittest.main()


class TestWebAccessRoute(unittest.TestCase):
    """The Settings route: turning web access on is refused while the screen
    agent runs, since the agent's browser can open this very page."""

    def setUp(self):
        from flask import Flask
        from backend.models import db
        from backend.api.settings_api import settings_bp
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
        db.init_app(self.app)
        self.app.register_blueprint(settings_bp)
        with self.app.app_context():
            db.create_all()
        self.client = self.app.test_client()
        prefix = settings_bp.url_prefix or ""
        self.url = f"{prefix}/web_access"

    def test_on_is_refused_while_the_agent_runs(self):
        with patch("backend.api.settings_api._screen_agent_running", return_value=True), \
             patch("backend.utils.agent_web_gate.enforce") as enforce:
            r = self.client.post(self.url, json={"allow_web_search": True})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Stop the agent first", r.get_data(as_text=True))
        enforce.assert_not_called()

    def test_off_is_always_allowed_and_applied_to_the_agent_browser(self):
        with patch("backend.api.settings_api._screen_agent_running", return_value=True), \
             patch("backend.utils.agent_web_gate.enforce", return_value={"allowed": False}) as enforce:
            r = self.client.post(self.url, json={"allow_web_search": False})
        self.assertEqual(r.status_code, 200)
        enforce.assert_called_once_with(False, "Settings: web access")

    def test_on_with_the_agent_idle(self):
        with patch("backend.api.settings_api._screen_agent_running", return_value=False), \
             patch("backend.utils.agent_web_gate.enforce", return_value={"allowed": True}) as enforce:
            r = self.client.post(self.url, json={"allow_web_search": True})
        self.assertEqual(r.status_code, 200)
        enforce.assert_called_once_with(True, "Settings: web access")
