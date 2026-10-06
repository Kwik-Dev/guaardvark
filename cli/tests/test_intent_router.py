"""Tests for natural-language / bare CLI routing in the REPL."""

import os
import stat

import pytest

from llx.command_catalog import BARE_ONLY_COMMANDS
from llx.intent_router import resolve_repl_line


@pytest.fixture
def pytest_only_path(tmp_path, monkeypatch):
    """PATH holding a single executable named pytest, so PATH lookups are deterministic."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "pytest"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


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

    def test_local_coding_intents(self, pytest_only_path):
        assert resolve_repl_line("read repl.py") == ("read", ["repl.py"])
        assert resolve_repl_line("grep TODO in cli") == ("grep", ["TODO in cli"])
        assert resolve_repl_line("ls cli/llx") == ("ls", ["cli/llx"])
        assert resolve_repl_line("edit foo.py fix the bug") == ("edit", ["foo.py fix the bug"])
        assert resolve_repl_line("run pytest") == ("run", ["pytest"])
        assert resolve_repl_line("todo add write tests") == ("todo", ["add write tests"])

    def test_image_generation_intents(self):
        assert resolve_repl_line("generate an image of the batmobile") == (
            "imagine",
            ["the batmobile"],
        )
        assert resolve_repl_line("create a picture of a sunset") == (
            "imagine",
            ["a sunset"],
        )
        assert resolve_repl_line("generate a video of waves") == (
            "video",
            ["waves"],
        )
        assert resolve_repl_line("make a music video from song.mp3 neon noir") == (
            "music-video",
            ["create", "--song", "song.mp3", "--style", "neon noir"],
        )
        assert resolve_repl_line("generate a music video 12 wet asphalt")[0] == "music-video"
        assert resolve_repl_line("start film crew") == ("film-crew", ["create"])
        assert resolve_repl_line("film this script INT. KITCHEN") == (
            "film-crew",
            ["create", "--script", "INT. KITCHEN"],
        )
        assert resolve_repl_line("start the film crew with INT. ROOM") == (
            "film-crew",
            ["create", "--script", "INT. ROOM"],
        )
        assert resolve_repl_line("generate csv report") == ("generate", ["csv", "report"])

    def test_plugin_and_gpu_intents(self):
        assert resolve_repl_line("list plugins") == ("plugins", ["list"])
        assert resolve_repl_line("gpu status") == ("gpu", ["status"])
        assert resolve_repl_line("what's using the gpu") == ("gpu", ["status"])
        assert resolve_repl_line("start comfyui") == ("plugins", ["start", "comfyui"])


class TestCommandWordsInEnglish:
    """Lines that only start with a command word go to chat; bare commands still run."""

    @pytest.mark.parametrize(
        "line",
        [
            "new ideas for a birthday party",
            "exit strategy for my startup",
            "quit smoking tips",
            "clear explanation of recursion please",
            "abort the mission plan, what are my options",
            "undo the damage of a bad review",
            "apply for a passport",
            "stop being so verbose",
            "start a story about dragons",
        ],
    )
    def test_session_command_word_with_more_text_is_chat(self, line, tmp_path):
        assert resolve_repl_line(line, cwd=tmp_path) is None

    @pytest.mark.parametrize("word", sorted(BARE_ONLY_COMMANDS))
    def test_bare_session_command_still_runs(self, word):
        assert resolve_repl_line(word) == (word, [])
        assert resolve_repl_line(word.capitalize()) == (word, [])

    def test_start_plugin_rule_still_wins(self):
        assert resolve_repl_line("start comfyui") == ("plugins", ["start", "comfyui"])

    def test_slash_session_commands_untouched(self):
        assert resolve_repl_line("/new") is None
        assert resolve_repl_line("/undo src/app.py") is None

    def test_run_needs_a_program_or_script(self, tmp_path, pytest_only_path):
        assert resolve_repl_line("run through the plan with me", cwd=tmp_path) is None
        assert resolve_repl_line("execute the plan we discussed", cwd=tmp_path) is None
        assert resolve_repl_line("run pytest -q", cwd=tmp_path) == ("run", ["pytest -q"])
        assert resolve_repl_line("sh pytest -q", cwd=tmp_path) == ("run", ["pytest -q"])

    def test_run_accepts_existing_script_in_repl_folder(self, tmp_path, pytest_only_path):
        script = tmp_path / "build.sh"
        script.write_text("#!/bin/sh\n")
        assert resolve_repl_line("run ./build.sh --fast", cwd=tmp_path) == (
            "run",
            ["./build.sh --fast"],
        )
        assert resolve_repl_line("run build.sh", cwd=tmp_path) == ("run", ["build.sh"])
        assert resolve_repl_line("run ./missing.sh", cwd=tmp_path) is None

    def test_test_needs_no_tail_or_an_existing_path(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("")
        assert resolve_repl_line("check the weather in Boston", cwd=tmp_path) is None
        assert resolve_repl_line("test my knowledge of French", cwd=tmp_path) is None
        assert resolve_repl_line("checkout the main branch", cwd=tmp_path) is None
        assert resolve_repl_line("check", cwd=tmp_path) == ("test", [])
        assert resolve_repl_line("test", cwd=tmp_path) == ("test", [])
        assert resolve_repl_line("pytest tests", cwd=tmp_path) == ("test", ["tests"])
        assert resolve_repl_line("test tests/test_x.py::test_one -q", cwd=tmp_path) == (
            "test",
            ["tests/test_x.py::test_one -q"],
        )

    def test_other_command_tree_lines_unchanged(self):
        assert resolve_repl_line("files list") == ("files", ["list"])
        assert resolve_repl_line("memory clear") == ("memory", ["clear"])
