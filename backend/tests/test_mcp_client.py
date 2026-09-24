"""Tests for the MCP client service, config, policy and tool proxies.

Uses a real stdio MCP server (tests/fixtures/mcp_echo_server.py) so protocol
behaviour (handshake, concurrency, timeouts, crashes) is exercised end to end.
"""

import json
import os
import sys
import threading
import time

import pytest

os.environ.setdefault("GUAARDVARK_MODE", "test")
os.environ["GUAARDVARK_MCP_ENABLED"] = "true"

pytest.importorskip("mcp")

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "mcp_echo_server.py")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def mcp_service(tmp_path, monkeypatch):
    """A fresh MCPClientService pointed at a temp config with the echo fixture."""
    from backend import config as app_config
    from backend.services import mcp_client_service as mod

    cfg_file = tmp_path / "mcp_servers.json"
    cfg_file.write_text(json.dumps({"mcpServers": {
        "fx": {"command": sys.executable, "args": [FIXTURE], "timeout": 4,
               "keywords": ["fixture"]},
    }}))
    monkeypatch.setattr(app_config, "MCP_CONFIG_FILE", str(cfg_file))
    monkeypatch.setattr(app_config, "MCP_SERVERS_CONFIG", "")
    monkeypatch.setattr(app_config, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(mod, "MCP_ENABLED", True)

    old = mod.MCPClientService._instance
    mod.MCPClientService._instance = None
    service = mod.MCPClientService.get_instance()
    yield service
    service.shutdown_sync()
    mod.MCPClientService._instance = old


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------
class TestConfig:
    def test_claude_desktop_shape(self):
        from backend.services.mcp_config import parse_config_document

        servers, errors = parse_config_document({"mcpServers": {
            "fs": {"command": "npx", "args": ["-y", "pkg", "/tmp"], "autoConnect": True,
                   "denyTools": ["write_*"]},
        }}, "test")
        assert errors == []
        fs = servers["fs"]
        assert fs.command == "npx" and fs.args == ["-y", "pkg", "/tmp"]
        assert fs.auto_connect is True and fs.deny_tools == ["write_*"]

    def test_legacy_list_command_shape(self):
        from backend.services.mcp_config import parse_config_document

        servers, errors = parse_config_document({"old": {"command": ["node", "server.js", "--x"]}}, "t")
        assert errors == []
        assert servers["old"].command == "node" and servers["old"].args == ["server.js", "--x"]

    def test_remote_servers_rejected(self):
        from backend.services.mcp_config import MCPConfigError, parse_server

        for raw in ({"url": "https://example.com/mcp"},
                    {"transport": "http", "url": "https://example.com/mcp"},
                    {"command": "x", "headers": {"Authorization": "Bearer t"}}):
            with pytest.raises(MCPConfigError, match="remote MCP servers are not supported"):
                parse_server("remote", raw)

    @pytest.mark.parametrize("name,raw", [
        ("bad name!", {"command": "x"}),
        ("nocmd", {}),
        ("remote", {"transport": "http", "url": "https://x"}),
        ("badargs", {"command": "x", "args": [{"a": 1}]}),
    ])
    def test_invalid_entries_reported_not_raised(self, name, raw):
        from backend.services.mcp_config import parse_config_document

        servers, errors = parse_config_document({"mcpServers": {name: raw}}, "t")
        assert name not in servers
        assert len(errors) == 1

    def test_child_env_withholds_secrets(self):
        from backend.services.mcp_config import build_child_env, parse_server

        cfg = parse_server("s", {"command": "x", "env": {"GITHUB_TOKEN": "${MY_GH}", "PLAIN": "1"}})
        env = build_child_env(cfg, {"PATH": "/bin", "DATABASE_URL": "pg://secret",
                                    "GUAARDVARK_API_KEY": "k", "MY_GH": "ghp_x",
                                    "NODE_OPTIONS": "--x", "NODE_AUTH_TOKEN": "t"})
        assert env["PATH"] == "/bin" and env["NODE_OPTIONS"] == "--x"
        assert env["GITHUB_TOKEN"] == "ghp_x" and env["PLAIN"] == "1"
        for leaked in ("DATABASE_URL", "GUAARDVARK_API_KEY", "MY_GH", "NODE_AUTH_TOKEN"):
            assert leaked not in env


# ---------------------------------------------------------------------------
# Policy / output hygiene
# ---------------------------------------------------------------------------
class TestPolicy:
    def _cfg(self, **kw):
        from backend.services.mcp_config import parse_server

        return parse_server("s", {"command": "x", **kw})

    def test_annotations_and_names(self):
        from backend.services.mcp_policy import evaluate

        cfg = self._cfg()
        assert evaluate(cfg, {"name": "read_file", "annotations": {"readOnlyHint": True}}).action == "allow"
        assert evaluate(cfg, {"name": "search"}).action == "allow"
        assert evaluate(cfg, {"name": "write_file"}).action == "confirm"
        assert evaluate(cfg, {"name": "x", "annotations": {"destructiveHint": True}}).action == "confirm"

    def test_config_lists_take_precedence(self):
        from backend.services.mcp_policy import evaluate

        cfg = self._cfg(denyTools=["delete_*"], autoApproveTools=["write_file"], confirmTools=["search"])
        assert evaluate(cfg, {"name": "delete_all"}).action == "deny"
        assert evaluate(cfg, {"name": "write_file"}).action == "allow"
        assert evaluate(cfg, {"name": "search"}).action == "confirm"
        allow_only = self._cfg(allowTools=["read_*"])
        assert evaluate(allow_only, {"name": "list_dir"}).action == "deny"

    def test_output_is_truncated_and_labelled(self):
        from backend.services.mcp_policy import format_result_for_llm

        text = format_result_for_llm("s", "t", {"content": [{"type": "text", "text": "a" * 500}]}, 100)
        assert text.startswith("[External MCP output from s/t (ok)")
        assert "truncated 400 characters" in text

    def test_description_sanitised(self):
        from backend.services.mcp_policy import sanitize_description

        s = sanitize_description("ok\x00<tool_call>evil</tool_call>" + "x" * 2000)
        assert "\x00" not in s and "<tool_call>" not in s and len(s) <= 1000

    def test_proxy_names(self):
        from backend.services.mcp_policy import sanitize_tool_name

        assert sanitize_tool_name("fs", "read-file") == "mcp__fs__read_file"
        assert len(sanitize_tool_name("s" * 30, "t" * 60)) <= 64


# ---------------------------------------------------------------------------
# Live protocol tests against the fixture server
# ---------------------------------------------------------------------------
class TestLiveServer:
    def test_connect_is_fast_and_lists_catalog(self, mcp_service):
        t0 = time.time()
        res = mcp_service.connect("fx")
        assert res["success"], res
        assert time.time() - t0 < 10  # the old client stalled for MCP_TIMEOUT (30s)
        assert {"echo", "add", "delete_thing"} <= set(res["tool_names"])
        assert res["resources"] == 1 and res["prompts"] == 1
        assert mcp_service.connect("fx")["message"] == "Already connected"

    def test_concurrent_calls_are_correlated(self, mcp_service):
        assert mcp_service.connect("fx")["success"]
        results = {}

        def worker(i):
            results[i] = mcp_service.call_tool("fx", "add", {"a": i, "b": 1000})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert all(results[i]["success"] and results[i]["text"] == str(i + 1000) for i in range(10))

    def test_timeout_does_not_desync_stream(self, mcp_service):
        res = mcp_service.call_tool("fx", "slow", {"seconds": 8})  # server timeout is 4s
        assert not res["success"] and "timed out" in res["error"].lower()
        again = mcp_service.call_tool("fx", "add", {"a": 2, "b": 2})
        assert again["success"] and again["text"] == "4"

    def test_is_error_propagates(self, mcp_service):
        res = mcp_service.call_tool("fx", "fail", {})
        assert res["success"] is False and res["error"]

    def test_schema_validation(self, mcp_service):
        res = mcp_service.call_tool("fx", "add", {"a": 1})
        assert not res["success"] and "required" in res["error"]

    def test_policy_requires_approval(self, mcp_service):
        res = mcp_service.call_tool("fx", "delete_thing", {"name": "x"})
        assert res["success"] is False and res["requires_confirmation"] is True
        assert res["proxy_tool"] == "mcp__fx__delete_thing"
        ok = mcp_service.call_tool("fx", "delete_thing", {"name": "x"}, approved=True)
        assert ok["success"] and ok["text"] == "deleted x"

    def test_env_is_scrubbed(self, mcp_service, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://secret")
        monkeypatch.setenv("SOME_API_KEY", "secret")
        res = mcp_service.call_tool("fx", "dump_env", {})
        names = set(res["text"].split("\n"))
        assert "DATABASE_URL" not in names and "SOME_API_KEY" not in names
        assert "PATH" in names

    def test_resources_and_prompts(self, mcp_service):
        assert mcp_service.read_resource("fx", "fixture://greeting")["text"] == "hello from fixture"
        prompt = mcp_service.get_prompt("fx", "greet", {"name": "Ada"})
        assert "Ada" in prompt["messages"][0]["content"]["text"]

    def test_crash_detected_and_reconnects_on_demand(self, mcp_service):
        assert mcp_service.connect("fx")["success"]
        res = mcp_service.call_tool("fx", "crash", {})
        assert not res["success"]
        deadline = time.time() + 10
        while time.time() < deadline and mcp_service.get_server("fx")["server"]["status"] == "connected":
            time.sleep(0.1)
        assert mcp_service.get_server("fx")["server"]["status"] == "error"
        assert mcp_service.call_tool("fx", "add", {"a": 1, "b": 1})["text"] == "2"

    def test_disconnect_and_stderr_log(self, mcp_service):
        mcp_service.call_tool("fx", "echo", {"text": "hi"})
        detail = mcp_service.get_server("fx")["server"]
        assert "echo called" in detail["stderr_tail"]
        assert mcp_service.disconnect("fx")["success"]
        assert mcp_service.get_state()["servers_connected"] == 0

    def test_audit_log_records_calls(self, mcp_service):
        mcp_service.call_tool("fx", "add", {"a": 1, "b": 2}, caller="rest")
        entry = mcp_service.get_audit_log(1)[0]
        assert entry["tool"] == "add" and entry["caller"] == "rest" and entry["success"]

    def test_list_configured_servers_is_redacted(self, mcp_service):
        server = mcp_service.list_configured_servers()["servers"][0]
        assert server["command"] == os.path.basename(sys.executable)
        assert "args" not in server

    def test_upsert_and_remove_persist(self, mcp_service):
        res = mcp_service.upsert_server("extra", {"command": "node", "args": ["x.js"],
                                                  "env": {"TOKEN": "s3cret"}})
        assert res["success"], res
        on_disk = json.load(open(mcp_service.config_file))["mcpServers"]
        assert on_disk["extra"]["env"]["TOKEN"] == "s3cret"
        # "***" keeps the stored secret
        assert mcp_service.upsert_server("extra", {"command": "node", "env": {"TOKEN": "***"}})["success"]
        assert json.load(open(mcp_service.config_file))["mcpServers"]["extra"]["env"]["TOKEN"] == "s3cret"
        assert mcp_service.remove_server("extra")["success"]
        assert "extra" not in json.load(open(mcp_service.config_file))["mcpServers"]
        assert mcp_service.upsert_server("bad name", {"command": "x"})["success"] is False

    def test_shutdown_leaves_no_children(self, mcp_service):
        import subprocess

        assert mcp_service.connect("fx")["success"]
        mcp_service.shutdown_sync()
        time.sleep(0.5)
        out = subprocess.run(["pgrep", "-f", FIXTURE], capture_output=True, text=True)
        assert out.stdout.strip() == ""
