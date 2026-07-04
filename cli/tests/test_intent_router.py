"""Tests for natural-language / bare CLI routing in the REPL."""

from llx.intent_router import resolve_repl_line


class TestResolveReplLine:
    def test_agents_list(self):
        assert resolve_repl_line("agents list") == ("agents", ["list"])

    def test_guaardvark_prefix(self):
        assert resolve_repl_line("guaardvark agents list") == ("agents", ["list"])

    def test_nl_list_agents(self):
        assert resolve_repl_line("list agents") == ("agents", ["list"])

    def test_status(self):
        assert resolve_repl_line("status") == ("status", [])

    def test_system_status(self):
        assert resolve_repl_line("system status") == ("status", [])

    def test_health_check(self):
        assert resolve_repl_line("health check") == ("health", [])

    def test_run_agent(self):
        assert resolve_repl_line("run agent general assistant") == (
            "agents",
            ["run", "general assistant"],
        )

    def test_chat_passthrough(self):
        assert resolve_repl_line("explain this codebase") is None

    def test_slash_passthrough(self):
        assert resolve_repl_line("/agents list") is None
