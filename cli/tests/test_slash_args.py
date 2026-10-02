"""Tests for slash command arg validation and sub-app dispatch."""

from unittest.mock import MagicMock, patch

import pytest

from llx.slash import SlashRouter

@pytest.fixture
def router():
    state = {
        "server": "http://localhost:5002",
        "session_id": "test-session",
        "message_count": 0,
        "agent_mode": False,
    }
    return SlashRouter(state)


# What search_knowledge_base returns when nothing matches.
_NO_PASSAGES = {"success": True, "result": {"success": True, "metadata": {"results": [], "retrieval": {}}}}


class TestSimpleSlashArgs:
    def test_search_without_query_shows_usage(self, router):
        with patch("llx.commands.search.get_client") as mock_get:
            router.dispatch("/search")
            mock_get.assert_not_called()

    def test_search_with_query_calls_api(self, router):
        with patch("llx.commands.search.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.execute_tool.return_value = _NO_PASSAGES
            mock_get.return_value = mock_client
            router.dispatch("/search hello world")
            mock_client.execute_tool.assert_called_once()
            name, params = mock_client.execute_tool.call_args[0]
            assert name == "search_knowledge_base"
            assert params["query"] == "hello world"

    def test_local_coding_commands_do_not_crash(self, router):
        # Dispatch several new local commands; they should succeed or show usage without backend
        for line in ["/pwd", "/ls .", "/todo list", "/grep foo ."]:
            router.dispatch(line)

    def test_search_passes_resolved_limit_not_optioninfo(self, router):
        with patch("llx.commands.search.get_client") as mock_get, patch(
            "llx.commands.search.output.print_markdown"
        ), patch("llx.commands.search.console.print"):
            mock_client = MagicMock()
            mock_client.execute_tool.return_value = _NO_PASSAGES
            mock_get.return_value = mock_client
            router.dispatch("/search guaardvark")
            mock_client.execute_tool.assert_called_once()
            _name, params = mock_client.execute_tool.call_args[0]
            assert params["top_k"] == 5


class TestSubappSlashArgs:
    def test_agents_without_subcommand_shows_usage(self, router):
        with patch("llx.main.app") as mock_app:
            router.dispatch("/agents")
            mock_app.assert_not_called()

    def test_agents_list_calls_subtyper(self, router):
        with patch("typer.main.get_command") as mock_get_command:
            click_cmd = MagicMock()
            mock_get_command.return_value = click_cmd
            with patch("llx.lite_mode.is_lite_mode", return_value=False):
                router.dispatch("/agents list")
            mock_get_command.assert_called_once()
            click_cmd.assert_called_once()
            assert click_cmd.call_args.kwargs["args"] == ["list"]
