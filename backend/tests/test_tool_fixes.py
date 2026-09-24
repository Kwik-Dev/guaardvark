"""Regression tests for registry-wide tool fixes (MCP hardening, phase 6)."""

import os
import types

import pytest

os.environ.setdefault("GUAARDVARK_MODE", "test")

from backend.services.agent_tools import ToolResult


# ---- path containment ------------------------------------------------------
def test_is_within_is_component_based_and_symlink_safe(tmp_path):
    from backend.utils.path_safety import is_within, safe_join

    root = tmp_path / "tmp"
    root.mkdir()
    sibling = tmp_path / "tmpX"
    sibling.mkdir()
    assert is_within(str(root / "a.txt"), [str(root)])
    assert not is_within(str(sibling / "a.txt"), [str(root)])  # old startswith() accepted this
    (root / "escape").symlink_to(tmp_path)
    assert not is_within(str(root / "escape" / "tmpX"), [str(root)])
    with pytest.raises(ValueError):
        safe_join(str(root), "../tmpX/evil.csv")
    assert safe_join(str(root), "ok.csv").endswith("ok.csv")


def test_desktop_service_path_check(tmp_path, monkeypatch):
    from backend.services import desktop_automation_service as das

    monkeypatch.setattr(das, "ALLOWED_PATHS", [str(tmp_path / "tmp")])
    (tmp_path / "tmp").mkdir()
    svc = das.DesktopAutomationService.__new__(das.DesktopAutomationService)
    assert svc._check_path_allowed(str(tmp_path / "tmp" / "f"))
    assert not svc._check_path_allowed(str(tmp_path / "tmpX" / "f"))


# ---- system_command ----------------------------------------------------------
@pytest.fixture
def syscmd(tmp_path, monkeypatch):
    from backend import config
    from backend.tools.system_tools import SystemCommandTool

    (tmp_path / "notes.txt").write_text("hello KEY=secret-in-notes\n")
    (tmp_path / ".env").write_text("DATABASE_URL=postgres://secret\n")
    monkeypatch.setattr(config, "GUAARDVARK_ROOT", tmp_path)
    monkeypatch.setattr(config, "ALLOWED_AUTOMATION_PATHS", [])
    return SystemCommandTool()


@pytest.mark.parametrize("command", [
    "find . -name '*.txt' -exec rm {} +",
    "find . -delete",
    "find . -fprint /tmp/x",
    "cat .env",
])
def test_system_command_blocks(syscmd, command):
    res = syscmd.execute(command=command)
    assert res.success is False, command


def test_system_command_allows_project_reads(syscmd, tmp_path):
    cwd = str(tmp_path)
    assert "hello" in syscmd.execute(command="cat notes.txt", cwd=cwd).output
    assert "notes.txt" in syscmd.execute(command="ls", cwd=cwd).output
    grep = syscmd.execute(command="grep -r secret .", cwd=cwd)
    assert "notes.txt" in grep.output and ".env" not in grep.output  # credential files excluded


# ---- media / desktop parameter handling -----------------------------------------------
def test_media_volume_accepts_numbers(monkeypatch):
    pytest.importorskip("backend.services.media_player_service")
    from backend.tools import media_tools

    seen = {}

    class FakeService:
        def set_volume(self, level):
            seen["level"] = level
            return {"success": True, "volume": level}

        def __getattr__(self, name):
            return lambda *a, **k: {"success": True}

    monkeypatch.setattr(media_tools, "MEDIA_CONTROL_ENABLED", True)
    monkeypatch.setattr(media_tools, "get_media_service", lambda: FakeService())
    res = media_tools.MediaVolumeTool().execute(level=50)  # used to raise AttributeError (.strip on int)
    assert res.success, res.error


@pytest.mark.parametrize("keys,expected", [
    (["ctrl", "c"], ["ctrl", "c"]),
    ("ctrl+c", ["ctrl", "c"]),
    (["ctrl+shift+t"], ["ctrl", "shift", "t"]),
    ("alt, tab", ["alt", "tab"]),
])
def test_hotkey_normalization(keys, expected):
    from backend.tools.desktop_tools import _normalize_hotkey

    assert _normalize_hotkey(keys) == expected


def test_file_watch_stop_needs_no_path():
    from backend.tools.desktop_tools import FileWatchTool

    assert FileWatchTool().can_execute(action="stop", watch_id="w1")


# ---- generation -------------------------------------------------------------------------
def test_bulk_csv_sends_handler_keys_and_never_fakes_queued(monkeypatch, tmp_path):
    from backend import config
    from backend.services import unified_file_generation as ufg
    from backend.tools.generation_tools import BulkCSVGeneratorTool

    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    captured = {}

    class FakeService:
        def generate(self, request):
            captured["request"] = request
            return types.SimpleNamespace(success=False, error="generator offline", job_id=None, output_path=None)

    monkeypatch.setattr(ufg, "UnifiedFileGenerationService", FakeService)
    res = BulkCSVGeneratorTool().execute(filename="out.csv", quantity="25", topic="SEO", client="Acme")
    req = captured["request"]
    assert req.content_spec["topics"][0] == "SEO - Part 1" and len(req.content_spec["topics"]) == 25
    assert req.content_spec["target_word_count"] == 600
    assert req.context_variables["client"] == "Acme"
    assert res.success is False and "generator offline" in res.error  # was: success=True, "queued"

    bad = BulkCSVGeneratorTool().execute(filename="../../etc/cron.d/x", quantity=1, topic="t")
    assert bad.success is False and "Invalid filename" in bad.error


# ---- LLM observation size ------------------------------------------------------------------
def test_observation_metadata_is_compacted():
    from backend.utils.agent_output_parser import format_tool_result_for_llm

    res = ToolResult(success=True, output="ok", metadata={"screenshot": "A" * 500000, "url": "https://x"})
    text = format_tool_result_for_llm("browser_screenshot", res)
    assert len(text) < 1000 and "screenshot omitted" in text and "https://x" in text


# ---- "Project folder only" (confine_tool_paths) ----------------------------------------
@pytest.fixture
def confined(monkeypatch):
    from backend.utils import settings_utils

    def _set(on):
        monkeypatch.setattr(settings_utils, "_confine_tool_paths", on)
    return _set


def test_system_command_unconfined_by_default(syscmd, confined, tmp_path):
    confined(False)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside\n")
    assert syscmd.execute(command=f"cat {outside}", cwd=str(tmp_path)).success


@pytest.mark.parametrize("command,cwd", [
    ("cat /etc/hostname", None),
    ("cat ../x", "."),
    ("ls", "/etc"),
    ("grep -f /etc/hostname x", None),
])
def test_system_command_confined(syscmd, confined, command, cwd, tmp_path):
    confined(True)
    res = syscmd.execute(command=command, cwd=(str(tmp_path) if cwd == "." else cwd))
    assert res.success is False and "outside" in res.error


def test_system_command_confined_allows_project(syscmd, confined):
    confined(True)
    assert "hello" in syscmd.execute(command="cat notes.txt").output  # cwd defaults to the project


def test_codegen_inputs_follow_the_setting(confined):
    from backend.tools.code_tools import _confine_candidates

    confined(False)
    assert _confine_candidates(["/etc/hostname"]) == ["/etc/hostname"]
    confined(True)
    assert _confine_candidates(["/etc/hostname", ".env"]) == []
