#!/usr/bin/env python3
"""Whisper.cpp installer: build-tool apt is the same problem as agent display.

A Flask request cannot type a sudo password. The old path ran
``sudo apt-get install cmake build-essential`` and returned
"Auto-install failed" on every stock Ubuntu box. It now uses the shared
privileged-apt helper (passwordless sudo, then pkexec, then a copyable
command).
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask  # noqa: E402

import backend.api.voice_api as voice_api  # noqa: E402


def _client():
    app = Flask(__name__)
    app.register_blueprint(voice_api.voice_bp)
    app.config["TESTING"] = True
    return app.test_client()


def _which_missing_cmake(cmd):
    if cmd in ("cmake", "make", "gcc"):
        return None
    return f"/usr/bin/{cmd}"


def _which_all_present(cmd):
    return f"/usr/bin/{cmd}"


class TestWhisperBuildDeps(unittest.TestCase):

    def test_reports_cmake_and_compiler_as_apt_packages(self):
        with patch("shutil.which", side_effect=_which_missing_cmake):
            missing = voice_api._missing_whisper_apt()
        self.assertEqual(missing, ["cmake", "build-essential"])

    def test_status_hands_back_a_command_when_sudo_needs_a_password(self):
        with patch("os.path.exists", return_value=False), \
             patch("shutil.which", side_effect=_which_missing_cmake), \
             patch.object(voice_api, "escalation_method", return_value="none"):
            body = _client().get("/api/voice/status").get_json()

        self.assertFalse(body["whisper_installed"])
        self.assertFalse(body["can_auto_install"])
        self.assertIn("cmake", body["manual_command"])
        self.assertIn("build-essential", body["manual_command"])

    def test_status_advertises_pkexec_when_the_desktop_can_prompt(self):
        with patch("os.path.exists", return_value=False), \
             patch("shutil.which", side_effect=_which_missing_cmake), \
             patch.object(voice_api, "escalation_method", return_value="pkexec"):
            body = _client().get("/api/voice/status").get_json()

        self.assertTrue(body["can_auto_install"])
        self.assertEqual(body["install_method"], "pkexec")
        self.assertIsNone(body["manual_command"])

    def test_does_not_probe_escalation_when_whisper_cli_is_present(self):
        with patch("os.path.exists", return_value=True), \
             patch.object(voice_api, "escalation_method") as probe:
            body = _client().get("/api/voice/status").get_json()

        probe.assert_not_called()
        self.assertTrue(body["whisper_installed"])
        self.assertTrue(body["can_auto_install"])
        self.assertIsNone(body["manual_command"])


class TestInstallWhisperApt(unittest.TestCase):

    def _install(self, apt_result, which=_which_missing_cmake):
        client = _client()
        with patch("os.path.exists", return_value=False), \
             patch("shutil.which", side_effect=which), \
             patch.object(voice_api, "run_privileged_apt", return_value=apt_result):
            return client.post("/api/voice/install-whisper")

    def test_no_desktop_hands_back_a_command(self):
        response = self._install(
            {"ok": False, "method": "none", "returncode": None, "stderr": ""}
        )
        self.assertEqual(response.status_code, 409)
        body = response.get_json()
        self.assertTrue(body["needs_manual_install"])
        self.assertIn("cmake", body["manual_command"])
        self.assertNotIn("Auto-install failed", body["error"])

    def test_dismissed_password_prompt_says_so(self):
        response = self._install(
            {"ok": False, "method": "pkexec", "returncode": 126, "stderr": ""}
        )
        self.assertEqual(response.status_code, 403)
        body = response.get_json()
        self.assertIn("dismissed", body["error"].lower())
        self.assertNotIn("manual_command", body)

    def test_apt_failure_includes_stderr_and_a_command(self):
        response = self._install({
            "ok": False, "method": "sudo", "returncode": 100,
            "stderr": "E: Unable to locate package cmake",
        })
        self.assertEqual(response.status_code, 500)
        body = response.get_json()
        self.assertTrue(body["needs_manual_install"])
        self.assertIn("Unable to locate package", body["stderr_tail"])

    def test_allowlist_rejects_unexpected_packages(self):
        from backend.utils.privileged_apt import apt_install_script
        with self.assertRaises(ValueError):
            apt_install_script(["cmake", "; rm -rf /"], allowed=voice_api._WHISPER_APT_PACKAGES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
