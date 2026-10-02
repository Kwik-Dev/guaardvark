"""generate_file refuses to "improve" a file it cannot read, and only then.

A name that exists somewhere (Guaardvark's own README.md, LICENSE, start.sh,
or an uploaded document) is not a request to change it: a new file of that
name goes to the outputs folder. The refusal needs a modify verb or an
existing-file reference aimed at the file.
"""

import sys
import types
from types import SimpleNamespace

import pytest

from backend.tools.generation_tools import FileGeneratorTool

# Names every install has at its root, plus an upload.
EXISTING = {
    "README.md", "LICENSE", "CHANGELOG.md", "docker-compose.yml",
    "setup.sh", "start.sh", "install.sh", "quality_gate.py",
}


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setattr(FileGeneratorTool, "_resolves", staticmethod(lambda name: name in EXISTING))
    return FileGeneratorTool()


@pytest.mark.parametrize("filename,description", [
    ("README.md", "A README for my new todo-list CLI written in Go"),
    ("README.md", "A README.md for my Go CLI that explains how to optimize builds"),
    ("LICENSE", "MIT license text for Jane Doe 2026"),
    ("CHANGELOG.md", "A changelog for version 1.0 of my app"),
    ("docker-compose.yml", "compose file with postgres and redis services"),
    ("app/config.yml", "config similar in spirit to docker-compose.yml but for my app"),
    ("setup.sh", "a setup script for a Debian box"),
    ("start.sh", "a bash script that starts my node server"),
    ("install.sh", "an installer for my dotfiles"),
    ("quality_gate.py", "a quality gate script that fails CI under 80% coverage"),
])
def test_new_file_with_an_existing_name_is_allowed(tool, filename, description):
    assert tool._detect_modify_existing(filename, description) is None


@pytest.mark.parametrize("filename,description,expected", [
    ("quality_gate_improved.py", "Improve the uploaded quality_gate.py with better structure", "quality_gate.py"),
    ("quality_gate.py", "Refactor this for readability", "quality_gate.py"),
    ("quality_gate.py", "improve it", "quality_gate.py"),
    ("README.md", "Improve the README", "README.md"),
    ("docker-compose.yml", "Update the docker-compose.yml to add a redis service", "docker-compose.yml"),
    ("new.yml", "Rewrite docker-compose.yml with healthchecks", "docker-compose.yml"),
    ("LICENSE", "Update the LICENSE year to 2027", "LICENSE"),
    ("summary.md", "Convert the uploaded quality_gate.py into prose", "quality_gate.py"),
])
def test_modify_request_on_an_existing_file_is_refused(tool, filename, description, expected):
    assert tool._detect_modify_existing(filename, description) == expected


def test_modify_wording_about_an_unresolvable_file_is_still_refused(tool):
    """The wording alone says the file exists and would be rewritten unseen."""
    assert tool._detect_modify_existing("out.py", "Improve this file so it runs faster") == "out.py"


def test_existence_is_not_checked_for_names_the_request_does_not_target(monkeypatch):
    looked_up = []

    def resolves(name):
        looked_up.append(name)
        return True

    monkeypatch.setattr(FileGeneratorTool, "_resolves", staticmethod(resolves))
    assert FileGeneratorTool()._detect_modify_existing("README.md", "A README for my Go CLI") is None
    assert looked_up == []


def test_execute_writes_a_new_readme_instead_of_refusing(tool, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.config.OUTPUT_DIR", str(tmp_path))
    fake_llm_service = types.ModuleType("backend.utils.llm_service")
    fake_llm_service.ChatMessage = lambda role, content: SimpleNamespace(role=role, content=content)
    fake_llm_service.MessageRole = SimpleNamespace(USER="user")
    monkeypatch.setitem(sys.modules, "backend.utils.llm_service", fake_llm_service)

    class Reply:
        class message:
            content = "# Todo CLI\n"

    class LLM:
        def chat(self, messages):
            return Reply()

    tool._llm = LLM()
    result = tool.execute(filename="README.md",
                          content_description="A README for my new todo-list CLI written in Go")
    assert result.success, result.error
    assert (tmp_path / "files" / "README.md").read_text() == "# Todo CLI"


def test_execute_refuses_improve_the_readme(tool, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.config.OUTPUT_DIR", str(tmp_path))
    result = tool.execute(filename="README.md", content_description="Improve the README")
    assert not result.success
    assert "codegen" in result.error
    assert not (tmp_path / "files" / "README.md").exists()
