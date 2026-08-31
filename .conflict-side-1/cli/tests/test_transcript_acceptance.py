"""Acceptance tests mirroring the chat.html CLI transcript scenarios."""

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from llx.intent_router import resolve_repl_line
from llx.main import app
from llx.slash import SlashRouter


runner = CliRunner()


def _router():
    return SlashRouter(
        {
            "server": "http://localhost:5000",
            "session_id": "test-session",
            "message_count": 0,
            "agent_mode": False,
        }
    )


class TestTranscriptAcceptance:
    def test_slash_search_usage_not_nameerror(self, capsys):
        with patch("llx.commands.search.get_client"):
            _router().dispatch("/search")
        out = capsys.readouterr().out
        assert "NameError" not in out
        assert "Usage: /search" in out

    def test_slash_doctor_no_optioninfo_error(self, monkeypatch):
        monkeypatch.setattr(
            "llx.commands.system.subprocess.run",
            lambda cmd: MagicMock(returncode=0),
        )
        monkeypatch.setattr("llx.commands.system.os.path.isfile", lambda p: True)

        with patch("llx.commands.system._find_project_root", return_value="/tmp/gv"):
            _router().dispatch("/doctor")

    def test_slash_rules_list_invokes_subtyper_not_sessions(self, monkeypatch):
        monkeypatch.setattr("llx.lite_mode.is_lite_mode", lambda: False)

        with patch("typer.main.get_command") as mock_gc:
            click_cmd = MagicMock()
            mock_gc.return_value = click_cmd
            _router().dispatch("/rules list")
            click_cmd.assert_called_once()
            assert click_cmd.call_args.kwargs["args"] == ["list"]

    def test_nl_generate_image_routes_to_imagine(self):
        assert resolve_repl_line("generate an image of the batmobile") == (
            "imagine",
            ["the batmobile"],
        )

    def test_nl_generate_picture_routes_to_imagine(self):
        assert resolve_repl_line("generate a picture of the batmobile") == (
            "imagine",
            ["the batmobile"],
        )

    def test_headless_rules_list_json_envelope(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.get.return_value = {"data": [{"id": 1, "name": "r1"}]}
        monkeypatch.setattr("llx.commands.rules.get_client", lambda server=None: mock_client)

        result = runner.invoke(app, ["rules", "list", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["status"] == "success"
        assert "rules" in payload["data"]

    def test_headless_health_json_envelope(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.server_url = "http://localhost:5000"
        mock_client.get.return_value = {"status": "ok", "version": "2.6.2", "uptime_seconds": 100}
        monkeypatch.setattr("llx.commands.system.get_client", lambda server=None: mock_client)

        result = runner.invoke(app, ["health", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["data"]["status"] == "ok"

    def test_ask_command_delegates_to_chat(self, monkeypatch):
        calls = []

        def fake_chat(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr("llx.main.chat", fake_chat)
        result = runner.invoke(app, ["ask", "hello there"])
        assert result.exit_code == 0
        assert calls
        assert calls[0].get("message") == "hello there"
