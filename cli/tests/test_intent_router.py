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

    def test_local_coding_intents(self, tmp_path, pytest_only_path):
        (tmp_path / "repl.py").write_text("")
        (tmp_path / "foo.py").write_text("")
        assert resolve_repl_line("read repl.py", cwd=tmp_path) == ("read", ["repl.py"])
        assert resolve_repl_line("grep TODO in cli", cwd=tmp_path) == ("grep", ["TODO in cli"])
        assert resolve_repl_line("ls cli/llx", cwd=tmp_path) == ("ls", ["cli/llx"])
        assert resolve_repl_line("edit foo.py fix the bug", cwd=tmp_path) == ("edit", ["foo.py fix the bug"])
        assert resolve_repl_line("run pytest", cwd=tmp_path) == ("run", ["pytest"])
        assert resolve_repl_line("todo add write tests", cwd=tmp_path) == ("todo", ["add write tests"])

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
            "export my notes",
            "export controls on chips explained",
            "history of rome",
            "history 3 times over, why does it repeat",
        ],
    )
    def test_session_command_word_with_more_text_is_chat(self, line, tmp_path):
        assert resolve_repl_line(line, cwd=tmp_path) is None

    @pytest.mark.parametrize("word", sorted(BARE_ONLY_COMMANDS))
    def test_bare_session_command_still_runs(self, word):
        assert resolve_repl_line(word) == (word, [])
        assert resolve_repl_line(word.capitalize()) == (word, [])

    def test_history_runs_bare_or_with_one_number(self):
        assert resolve_repl_line("history") == ("history", [])
        assert resolve_repl_line("History") == ("history", [])
        assert resolve_repl_line("history 3") == ("history", ["3"])
        assert resolve_repl_line("history three") is None
        assert resolve_repl_line("history -1") is None

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


class TestEnglishSentencesAreChat:
    """A sentence that starts with a command word is chat unless the rest of the
    line has the command's shape: an existing file, a subcommand, a command name."""

    @pytest.fixture
    def folder(self, tmp_path):
        (tmp_path / "foo.py").write_text("print(1)\n")
        (tmp_path / "notes.md").write_text("")
        (tmp_path / "cli").mkdir()
        return tmp_path

    @pytest.mark.parametrize(
        "line",
        [
            "help me write a cover letter",
            "help me",
            "fix my cover letter wording",
            "update the plan for next week",
            "implement a linked list in C",
            "find a good pizza place nearby",
            "search for flights to Lisbon",
            "show me how closures work",
            "read me a bedtime story",
            "view from the top of the tower",
            "list three ideas for dinner",
            "listen to this idea",
            "directions to the station",
            "tasks for today",
            "taskbar keeps disappearing",
            "generate ideas for a party",
            "rules for a board game",
            "agents of shield",
            "memory of my grandmother",
            "gpu prices are crazy",
            "tools for woodworking",
            "suggest a name for my dog",
            "doctor who episodes",
            "status of my order",
            "web design trends this year",
        ],
    )
    def test_sentence_is_chat(self, line, folder):
        assert resolve_repl_line(line, cwd=folder) is None

    @pytest.mark.parametrize(
        "line, expected",
        [
            ("read foo.py", ("read", ["foo.py"])),
            ("read foo.py 10 20", ("read", ["foo.py 10 20"])),
            ("show notes.md", ("read", ["notes.md"])),
            ("view file foo.py", ("read", ["foo.py"])),
            ("cat anything.txt", ("read", ["anything.txt"])),
            ("fix foo.py the off-by-one", ("edit", ["foo.py the off-by-one"])),
            ("update notes.md add a heading", ("edit", ["notes.md add a heading"])),
            ("find TODO", ("grep", ["TODO"])),
            ("find TODO in cli", ("grep", ["TODO in cli"])),
            ("search resolve_repl_line", ("grep", ["resolve_repl_line"])),
            ("grep anything at all", ("grep", ["anything at all"])),
            ("list", ("ls", [])),
            ("list files", ("ls", [])),
            ("list cli", ("ls", ["cli"])),
            ("list files in cli", ("ls", ["cli"])),
            ("ls -la", ("ls", ["-la"])),
            ("todo", ("todo", [])),
            ("tasks list", ("todo", ["list"])),
            ("todo done 2", ("todo", ["done 2"])),
            ("help", ("help", [])),
            ("help agents", ("help", ["agents"])),
            ("web images", ("web", ["images"])),
            ("tools", ("tools", [])),
            ("doctor --repair", ("doctor", ["--repair"])),
            ("generate csv report", ("generate", ["csv", "report"])),
            ("files list", ("files", ["list"])),
            ("agents info 3", ("agents", ["info", "3"])),
            ("logs tail", ("logs", ["tail"])),
        ],
    )
    def test_command_form_still_runs(self, line, expected, folder):
        assert resolve_repl_line(line, cwd=folder) == expected
