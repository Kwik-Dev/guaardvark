"""REPL sub-app dispatch must invoke sub-typers directly, not root app()."""

import json
from unittest.mock import MagicMock, patch

import pytest

from llx.slash import SlashRouter


@pytest.fixture
def router():
    return SlashRouter(
        {
            "server": "http://localhost:5000",
            "session_id": "test-session",
            "message_count": 0,
            "agent_mode": False,
        }
    )


class TestReplSubappDispatch:
    def test_rules_list_invokes_subtyper(self, router, monkeypatch):
        monkeypatch.setattr("llx.lite_mode.is_lite_mode", lambda: False)

        with patch("typer.main.get_command") as mock_get_command:
            click_cmd = MagicMock()
            mock_get_command.return_value = click_cmd

            router.dispatch("/rules list")

            mock_get_command.assert_called_once()
            click_cmd.assert_called_once()
            assert click_cmd.call_args.kwargs["args"] == ["list"]
            assert click_cmd.call_args.kwargs["standalone_mode"] is False

    def test_search_without_query_shows_usage(self, router):
        with patch("llx.commands.search.get_client") as mock_get:
            router.dispatch("/search")
            mock_get.assert_not_called()

    def test_search_usage_message_content(self, router, capsys):
        with patch("llx.commands.search.get_client"):
            router.dispatch("/search")
        captured = capsys.readouterr()
        assert "Usage: /search" in captured.out

    def test_subapp_uses_get_command_not_root_app(self, router, monkeypatch):
        monkeypatch.setattr("llx.lite_mode.is_lite_mode", lambda: False)

        with patch("typer.main.get_command") as mock_get_command, patch("llx.main.app") as mock_root_app:
            click_cmd = MagicMock()
            mock_get_command.return_value = click_cmd
            router.dispatch("/quality scorecard --json")
            mock_get_command.assert_called_once()
            click_cmd.assert_called_once()
            mock_root_app.assert_not_called()

    def test_headless_rules_list_json(self, monkeypatch):
        from typer.testing import CliRunner

        from llx.main import app

        mock_client = MagicMock()
        mock_client.get.return_value = {"data": [{"id": 1, "name": "r1"}]}
        monkeypatch.setattr("llx.commands.rules.get_client", lambda server=None: mock_client)

        result = CliRunner().invoke(app, ["rules", "list", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["status"] == "success"
        assert "rules" in payload["data"]

    def test_headless_health_not_sessions(self, monkeypatch):
        from typer.testing import CliRunner

        from llx.main import app

        mock_client = MagicMock()
        mock_client.get.return_value = {"status": "ok", "version": "2.6.2", "uptime_seconds": 10}
        monkeypatch.setattr("llx.commands.system.get_client", lambda server=None: mock_client)

        result = CliRunner().invoke(app, ["health", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["status"] == "success"
        assert payload["data"]["status"] == "ok"
        assert "preview" not in result.stdout
