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


class TestSimpleSlashArgs:
    def test_search_without_query_shows_usage(self, router):
        with patch("llx.commands.search.get_client") as mock_get:
            router.dispatch("/search")
            mock_get.assert_not_called()

    def test_search_with_query_calls_api(self, router):
        with patch("llx.commands.search.get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.post.return_value = {"answer": "hi", "sources": []}
            mock_get.return_value = mock_client
            router.dispatch("/search hello world")
            mock_client.post.assert_called_once()
            payload = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1].get("json")
            assert payload["query"] == "hello world"

    def test_local_coding_commands_do_not_crash(self, router):
        # Dispatch several new local commands; they should succeed or show usage without backend
        for line in ["/pwd", "/ls .", "/todo list", "/grep foo ."]:
            router.dispatch(line)

    def test_search_passes_resolved_limit_not_optioninfo(self, router):
        with patch("llx.commands.search.get_client") as mock_get, patch(
            "llx.commands.search.output.print_markdown"
        ), patch("llx.commands.search.console.print"):
            mock_client = MagicMock()
            mock_client.post.return_value = {
                "answer": "",
                "sources": [{"source_document": "a", "score": 0.9}],
            }
            mock_get.return_value = mock_client
            router.dispatch("/search guaardvark")
            mock_client.post.assert_called_once()


class TestSubappSlashArgs:
    def test_agents_without_subcommand_shows_usage(self, router):
        with patch("llx.main.app") as mock_app:
            router.dispatch("/agents")
            mock_app.assert_not_called()

    def test_agents_list_calls_typer(self, router):
        with patch("llx.main.app") as mock_app:
            router.dispatch("/agents list")
            mock_app.assert_called_once()
            assert mock_app.call_args.kwargs["args"] == ["agents", "list"]
