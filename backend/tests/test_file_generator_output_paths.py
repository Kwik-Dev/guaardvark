from pathlib import Path
import sys
import types
from types import SimpleNamespace


class _FakeMessage:
    content = "export default function Demo() { return null; }"


class _FakeResponse:
    message = _FakeMessage()


class _FakeLLM:
    def chat(self, messages):
        return _FakeResponse()


def _patch_llm_message_types(monkeypatch):
    fake_utils = types.ModuleType("backend.utils")
    fake_utils.__path__ = []
    fake_llm_service = types.ModuleType("backend.utils.llm_service")

    fake_llm_service.ChatMessage = lambda role, content: SimpleNamespace(
        role=role,
        content=content,
    )
    fake_llm_service.MessageRole = SimpleNamespace(USER="user")
    fake_utils.llm_service = fake_llm_service

    monkeypatch.setitem(sys.modules, "backend.utils", fake_utils)
    monkeypatch.setitem(sys.modules, "backend.utils.llm_service", fake_llm_service)


def test_generate_file_creates_nested_output_paths(tmp_path, monkeypatch):
    from backend.tools.generation_tools import FileGeneratorTool

    monkeypatch.setattr("backend.config.OUTPUT_DIR", str(tmp_path))
    _patch_llm_message_types(monkeypatch)

    tool = FileGeneratorTool()
    tool._llm = _FakeLLM()

    result = tool.execute(
        filename="frontend/src/pages/SettingsPage.jsx",
        content_description="Create a demo React page",
        file_type="javascript",
        save_to_disk=True,
    )

    expected_path = tmp_path / "files" / "frontend" / "src" / "pages" / "SettingsPage.jsx"
    assert result.success is True
    assert Path(result.output["output_path"]) == expected_path
    assert expected_path.read_text() == _FakeMessage.content
    assert result.metadata["destination"] == "output_dir"


def test_generate_file_refuses_to_modify_existing_uploaded_file(tmp_path, monkeypatch):
    """generate_file must REFUSE to 'improve' a file it cannot read, rather than
    fabricate a version of it.

    Regression for the placebo case: an uploaded quality_gate.py was 'improved'
    into unrelated boilerplate because generate_file never reads source. The
    guard should redirect to codegen/edit_code instead of generating.
    """
    from backend.tools.generation_tools import FileGeneratorTool

    monkeypatch.setattr("backend.config.OUTPUT_DIR", str(tmp_path))

    # Simulate an uploaded file named quality_gate.py being resolvable.
    fake_resolver = types.ModuleType("backend.utils.uploaded_file_resolver")
    fake_resolver.find_uploaded_file = (
        lambda name: ("real file contents", None) if name.endswith("quality_gate.py") else None
    )
    monkeypatch.setitem(sys.modules, "backend.utils.uploaded_file_resolver", fake_resolver)

    tool = FileGeneratorTool()
    tool._llm = _FakeLLM()

    result = tool.execute(
        filename="quality_gate_improved.py",
        content_description="Improve the uploaded quality_gate.py with better structure",
        file_type="PY",
        save_to_disk=True,
    )

    assert result.success is False
    assert "codegen" in result.error
    # Nothing should have been written to disk.
    assert not (tmp_path / "files" / "quality_gate_improved.py").exists()


def test_generate_file_rejects_paths_outside_output_dir(tmp_path, monkeypatch):
    from backend.tools.generation_tools import FileGeneratorTool

    monkeypatch.setattr("backend.config.OUTPUT_DIR", str(tmp_path))

    tool = FileGeneratorTool()
    tool._llm = _FakeLLM()

    result = tool.execute(
        filename="../frontend/src/pages/SettingsPage.jsx",
        content_description="Attempt path traversal",
        file_type="javascript",
        save_to_disk=True,
    )

    assert result.success is False
    assert "Filename cannot contain" in result.error
    assert not (tmp_path.parent / "frontend").exists()
