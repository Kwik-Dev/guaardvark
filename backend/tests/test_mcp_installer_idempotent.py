"""``python -m backend.mcp install`` run more than once, and over entries the
user has customised; the ``config`` snippets; doctor's client scan.

Every test runs against a temporary home directory, and the client CLIs are
replaced by a recorder: nothing here reads or writes a real client config.
"""

import json
import shlex
from types import SimpleNamespace

import pytest

from backend.mcp import cli as snippets
from backend.mcp import doctor, installer

SECRET = "sk-test-0001"
STALE = {"command": "sh", "args": ["-c", "cd /old && exec /old/python -m backend.mcp"]}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(installer.platform, "system", lambda: "Linux")
    return tmp_path


@pytest.fixture
def cli(monkeypatch):
    """Stands in for the client CLIs. ``cli.calls`` is every argv the installer
    ran; ``cli.replies`` queues (exit code, stderr) answers, success otherwise."""
    recorder = SimpleNamespace(calls=[], replies=[])

    def run(argv, **_kwargs):
        recorder.calls.append(list(argv))
        code, err = recorder.replies.pop(0) if recorder.replies else (0, "")
        return SimpleNamespace(returncode=code, stdout="", stderr=err)

    monkeypatch.setattr(installer.subprocess, "run", run)
    return recorder


def _launch():
    command, args = installer._shell_wrapper()
    return command, args


def _write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document if isinstance(document, str) else json.dumps(document))
    return path


# ---- JSON-file clients: the user's additions survive a re-run -----------------------------
def _json_clients(home):
    return {
        "cursor": (home / ".cursor/mcp.json", "mcpServers"),
        "claude-desktop": (home / ".config/Claude/claude_desktop_config.json", "mcpServers"),
        "gemini": (home / ".gemini/settings.json", "mcpServers"),
    }


@pytest.mark.parametrize("client", ["cursor", "claude-desktop", "gemini"])
def test_a_rerun_keeps_what_the_user_added_to_the_entry(home, cli, client):
    path, table = _json_clients(home)[client]
    mine = {**STALE, "env": {"GUAARDVARK_API_KEY": SECRET}, "disabled": False, "autoApprove": ["web_search"]}
    _write(path, {table: {"other": {"command": "x"}, "guaardvark": mine}, "theme": "dark"})

    result = installer.install_client(client, force=True)

    command, args = _launch()
    data = json.loads(path.read_text())
    assert result.status == "installed"
    assert data[table]["guaardvark"] == {**mine, "command": command, "args": args}
    assert data[table]["other"] == {"command": "x"} and data["theme"] == "dark"
    assert cli.calls == []


def test_opencode_keeps_its_environment_and_a_disabled_entry_stays_disabled(home, cli):
    path = _write(home / ".config/opencode/opencode.json", {"mcp": {"guaardvark": {
        "type": "local", "command": ["sh", "-c", "old"], "enabled": False,
        "environment": {"GUAARDVARK_API_KEY": SECRET}}}})

    result = installer.install_client("opencode", force=True)

    command, args = _launch()
    entry = json.loads(path.read_text())["mcp"]["guaardvark"]
    assert entry == {"type": "local", "command": [command, *args], "enabled": False,
                     "environment": {"GUAARDVARK_API_KEY": SECRET}}
    assert "switched off" in result.detail


@pytest.mark.parametrize("existing,expected_command", [
    ({"command": {"path": "sh", "args": ["-c", "old"], "env": {"K": "v"}}, "settings": {"x": 1}}, "nested"),
    ({"command": "python", "args": ["-m", "backend.mcp"], "env": {"K": "v"}, "settings": {"x": 1}}, "flat"),
    (None, "nested"),
])
def test_zed_is_merged_in_the_form_the_entry_already_uses(home, cli, existing, expected_command):
    servers = {"guaardvark": existing} if existing else {}
    path = _write(home / ".config/zed/settings.json", {"context_servers": servers})

    installer.install_client("zed", force=True)

    command, args = _launch()
    entry = json.loads(path.read_text())["context_servers"]["guaardvark"]
    if expected_command == "flat":
        assert (entry["command"], entry["args"], entry["env"]) == (command, args, {"K": "v"})
    else:
        assert (entry["command"]["path"], entry["command"]["args"]) == (command, args)
        assert entry["command"]["env"] == ({"K": "v"} if existing else {})
    if existing:
        assert entry["settings"] == {"x": 1}


def test_a_remote_entry_becomes_a_stdio_entry(home, cli):
    path = _write(home / ".cursor/mcp.json", {"mcpServers": {"guaardvark": {
        "type": "http", "url": "http://127.0.0.1:8788/mcp", "headers": {"A": "b"}, "env": {"K": "v"}}}})

    installer.install_client("cursor", force=True)

    command, args = _launch()
    assert json.loads(path.read_text())["mcpServers"]["guaardvark"] == {
        "type": "stdio", "env": {"K": "v"}, "command": command, "args": args}


@pytest.mark.parametrize("client", ["cursor", "claude-desktop", "gemini", "opencode", "zed"])
def test_a_second_run_reports_already_configured_and_rewrites_nothing(home, cli, client):
    assert installer.install_client(client, force=True).status == "installed"
    path = {**{k: v[0] for k, v in _json_clients(home).items()},
            "opencode": home / ".config/opencode/opencode.json",
            "zed": home / ".config/zed/settings.json"}[client]
    written = path.read_text()
    stamp = path.stat().st_mtime_ns

    again = installer.install_client(client, force=True)

    assert again.status == "unchanged" and again.detail.startswith(installer.ALREADY_CONFIGURED)
    assert path.read_text() == written and path.stat().st_mtime_ns == stamp


def test_a_config_with_comments_is_refused_and_left_as_it_was(home, cli):
    text = '{\n  // mine\n  "context_servers": {}\n}\n'
    path = _write(home / ".config/zed/settings.json", text)

    assert installer.install_client("zed", force=True).status == "failed"
    assert path.read_text() == text


# ---- Claude Code: `claude mcp add` refuses an existing name ----------------------------------
def _claude_json(home, entry=None):
    servers = {"guaardvark": entry} if entry else {}
    return _write(home / ".claude.json", {"numStartups": 3, "mcpServers": servers})


def test_claude_code_with_no_entry_is_added_without_a_removal(home, cli):
    _claude_json(home)
    command, args = _launch()

    assert installer.install_client("claude-code", force=True).status == "installed"
    assert cli.calls == [["claude", "mcp", "add", "--scope", "user", "guaardvark", "--", command, *args]]


def test_claude_code_stale_entry_is_removed_then_added_with_its_environment(home, cli):
    _claude_json(home, {"type": "stdio", **STALE, "env": {"GUAARDVARK_API_KEY": SECRET}})
    command, args = _launch()

    result = installer.install_client("claude-code", force=True)

    assert result.status == "installed"
    assert cli.calls == [
        ["claude", "mcp", "remove", "--scope", "user", "guaardvark"],
        ["claude", "mcp", "add", "--scope", "user", "guaardvark",
         "-e", f"GUAARDVARK_API_KEY={SECRET}", "--", command, *args],
    ]
    assert SECRET not in result.detail and "GUAARDVARK_API_KEY=***" in result.detail


def test_claude_code_matching_entry_is_already_configured_and_runs_nothing(home, cli):
    command, args = _launch()
    _claude_json(home, {"type": "stdio", "command": command, "args": args, "env": {}})

    result = installer.install_client("claude-code", force=True)

    assert result.status == "unchanged" and result.detail.startswith(installer.ALREADY_CONFIGURED)
    assert cli.calls == []


def test_claude_code_unreadable_config_tries_a_removal_and_ignores_its_failure(home, cli):
    cli.replies[:] = [(1, 'No MCP server named "guaardvark" in user scope'), (0, "")]

    assert installer.install_client("claude-code", force=True).status == "installed"
    assert [argv[:3] for argv in cli.calls] == [["claude", "mcp", "remove"], ["claude", "mcp", "add"]]


def test_claude_code_refusal_is_reported_with_what_the_removal_said(home, cli):
    _claude_json(home, {"type": "stdio", **STALE})
    cli.replies[:] = [(1, "permission denied"), (1, "MCP server guaardvark already exists in user config")]

    result = installer.install_client("claude-code", force=True)

    assert result.status == "failed"
    assert "already exists" in result.detail and "permission denied" in result.detail


def test_claude_code_follows_a_relocated_config_directory(home, cli, monkeypatch):
    elsewhere = home / "elsewhere"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(elsewhere))
    command, args = _launch()
    _write(elsewhere / ".claude.json", {"mcpServers": {"guaardvark": {"command": command, "args": args}}})

    assert installer.install_client("claude-code", force=True).status == "unchanged"
    assert cli.calls == []


# ---- Codex, Grok, Antigravity: their `mcp add` replaces the entry -------------------------
_TOML_STALE = (
    '[mcp_servers.guaardvark]\ncommand = "sh"\nargs = ["-c", "cd /old && exec /old/python -m backend.mcp"]\n'
    f'startup_timeout_sec = 40\n\n[mcp_servers.guaardvark.env]\nGUAARDVARK_API_KEY = "{SECRET}"\n'
)


def _cli_client_cases(home):
    command, args = _launch()
    key = f"GUAARDVARK_API_KEY={SECRET}"
    current_toml = f"[mcp_servers.guaardvark]\ncommand = {json.dumps(command)}\nargs = {json.dumps(args)}\n"
    return {
        "codex": (home / ".codex/config.toml", _TOML_STALE, current_toml,
                  ["codex", "mcp", "add", "guaardvark", "--env", key, "--", command, *args]),
        "grok": (home / ".grok/config.toml", _TOML_STALE, current_toml,
                 ["grok", "mcp", "add", "--scope", "user", "-e", key, "guaardvark", command, "--", *args]),
        "antigravity": (
            home / ".gemini/config/mcp_config.json",
            json.dumps({"mcpServers": {"guaardvark": {**STALE, "env": {"GUAARDVARK_API_KEY": SECRET},
                                                      "timeoutSeconds": 90}}}),
            json.dumps({"mcpServers": {"guaardvark": {"command": command, "args": args}}}),
            ["agy", "mcp", "add", "--env", key, "guaardvark", "--", command, *args]),
    }


@pytest.mark.parametrize("client", ["codex", "grok", "antigravity"])
def test_cli_clients_carry_the_environment_and_say_what_they_could_not(home, cli, client):
    path, stale, _current, expected = _cli_client_cases(home)[client]
    _write(path, stale)

    result = installer.install_client(client, force=True)

    assert result.status == "installed" and cli.calls == [expected]
    assert SECRET not in result.detail and "not carried over" in result.detail


@pytest.mark.parametrize("client", ["codex", "grok", "antigravity"])
def test_cli_clients_with_a_matching_entry_run_nothing(home, cli, client):
    path, _stale, current, _expected = _cli_client_cases(home)[client]
    _write(path, current)

    assert installer.install_client(client, force=True).status == "unchanged"
    assert cli.calls == []


def test_codex_follows_codex_home(home, cli, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(home / "cx"))
    command, args = _launch()
    _write(home / "cx/config.toml",
           f"[mcp_servers.guaardvark]\ncommand = {json.dumps(command)}\nargs = {json.dumps(args)}\n")

    assert installer.install_client("codex", force=True).status == "unchanged"


# ---- the command as a whole ------------------------------------------------------------------
def test_run_install_twice_exits_zero_and_the_second_pass_changes_nothing(home, cli, capsys):
    command, args = _launch()
    _claude_json(home, {"type": "stdio", "command": command, "args": args})
    clients = ["cursor", "claude-code", "opencode", "gemini"]

    assert installer.run_install(clients) == 0
    first = capsys.readouterr().out
    assert installer.run_install(clients) == 0
    second = capsys.readouterr().out

    assert "Restart the client" in first and "Restart the client" not in second
    assert second.count(installer.ALREADY_CONFIGURED) == len(clients)
    assert cli.calls == []


# ---- `config --client`: what to paste or run, and where ------------------------------------
def test_the_claude_code_instruction_is_its_own_cli_not_a_file_it_never_reads(home, capsys):
    assert snippets.print_snippet("claude-code") == 0
    out = capsys.readouterr().out

    command, args = _launch()
    line = next(text for text in out.splitlines() if text.startswith("claude "))
    assert shlex.split(line) == ["claude", "mcp", "add", "--scope", "user", "guaardvark", "--", command, *args]
    assert "mcp_servers.json" not in out and ".mcp.json" in out


@pytest.mark.parametrize("client", snippets.CLIENT_CHOICES)
def test_every_snippet_launches_the_way_install_does(home, capsys, client):
    assert snippets.print_snippet(client) == 0
    out = capsys.readouterr().out

    entry = next(iter(json.loads(out[out.index("{"):]).values()))["guaardvark"]
    launch = entry["command"] if isinstance(entry["command"], dict) else entry
    command, args = _launch()
    assert [launch.get("path", launch.get("command")), *launch["args"]] == [command, *args]
    assert "cwd" not in launch


# ---- doctor scans every client install can set up ------------------------------------------
_GONE = ["-c", "cd /gone && exec /gone/python -m backend.mcp"]


def test_doctor_checks_the_codex_opencode_and_antigravity_entries(home, capsys):
    _write(home / ".codex/config.toml",
           f'[mcp_servers.guaardvark]\ncommand = "sh"\nargs = {json.dumps(_GONE)}\n')
    _write(home / ".config/opencode/opencode.json",
           {"mcp": {"guaardvark": {"type": "local", "command": ["sh", *_GONE]}}})
    _write(home / ".gemini/config/mcp_config.json",
           {"mcpServers": {"guaardvark": {"command": "sh", "args": _GONE}}})

    assert doctor._check_clients() is False
    out = capsys.readouterr().out
    for label in ("codex", "opencode", "antigravity"):
        assert f"[FAIL] client: {label} — missing path: /gone" in out


def test_doctor_names_a_config_it_could_not_parse(home, capsys):
    _write(home / ".config/zed/settings.json", '{\n  // mine\n  "context_servers": {}\n}\n')

    doctor._check_clients()
    out = capsys.readouterr().out
    assert "[warn] client: zed" in out and "not checked" in out


def test_doctor_covers_every_client_install_supports():
    labels = " ".join(label for label, _path, _tables in doctor._client_config_sources())
    assert [client for client in installer.CLIENTS if client not in labels] == []
