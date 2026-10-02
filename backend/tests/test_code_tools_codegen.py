"""codegen and analyze_code with the model stubbed: what codegen saves for each
shape of model reply. No Ollama, GPU, network or database: the LLM service
module is replaced and output goes to tmp_path.

The case tables are module-level so a standalone check can import them and run
the same cases without pytest."""
import os
import sys
import types
from types import SimpleNamespace

import pytest

import backend.tools.code_tools as ct


class StubLLM:
    def __init__(self, reply="x = 1", model="stub-model", context_window=8192):
        self.reply, self.model, self.context_window = reply, model, context_window
        self.prompts = []

    def chat(self, messages):
        self.prompts.append(messages[0].content)
        return SimpleNamespace(message=SimpleNamespace(content=self.reply))


def fake_llm_service(state):
    """A stand-in for backend.utils.llm_service whose get_default_llm returns
    state["llm"]."""
    fake = types.ModuleType("backend.utils.llm_service")
    fake.ChatMessage = lambda role, content: SimpleNamespace(role=role, content=content)
    fake.MessageRole = SimpleNamespace(USER="user")
    fake.get_default_llm = lambda: state["llm"]
    return fake


@pytest.fixture
def llm(tmp_path, monkeypatch):
    """Installs a stub LLM service and returns a function that sets the client
    the next calls receive."""
    monkeypatch.setattr("backend.config.OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setattr("backend.config.UPLOAD_DIR", str(tmp_path / "uploads"))
    (tmp_path / "uploads").mkdir()
    state = {"llm": StubLLM()}
    monkeypatch.setitem(sys.modules, "backend.utils.llm_service", fake_llm_service(state))

    def use(stub):
        state["llm"] = stub
        return stub

    return use


def _generate(tmp_path, filename="out.py", **kwargs):
    kwargs.setdefault("instructions", "write a greeting script")
    result = ct.CodeGeneratorTool().execute(output_filename=filename, **kwargs)
    return result, tmp_path / "outputs" / "code" / filename


FILE = "print('hi')"
JS_FILE = "console.log(1)"

# (output file, model reply, what must be saved). The code sits in a fence with
# chat around it; only the code is the file.
CHAT_REPLIES = [
    ("out.py", FILE, FILE),
    ("out.py", f"```python\n{FILE}\n```", FILE),
    ("out.py", f"Here is the complete file:\n\n```python\n{FILE}\n```", FILE),
    ("out.py", f"```python\n{FILE}\n```\n\nThis version adds a greeting.", FILE),
    ("out.py", f"Sure! Here's the file:\n```py\n{FILE}\n```\nIt prints a greeting.", FILE),
    ("out.py", f"``` Python3 \n{FILE}\n```", FILE),
    ("out.py", f"~~~python\n{FILE}\n~~~", FILE),
    ("out.py", f"```python\n{FILE}", FILE),
    ("cfg.json", 'Here is the JSON:\n```json\n{"a": 1}\n```', '{"a": 1}'),
]

# Replies with more than one fenced block. The file is the first block tagged
# with the output file's language; failing that the first untagged block; a
# block tagged as another language (how to install or run it) only when there
# is nothing else. Block length plays no part: the usage lines here are longer
# than the file.
TWO_BLOCK_REPLIES = [
    # file first, usage second
    ("out.py", f"Here is the file:\n```python\n{FILE}\n```\nRun it:\n```bash\npython out.py\n```", FILE),
    ("out.py", f"```python\n{FILE}\n```\n```bash\npython out.py --verbose\n```", FILE),
    ("out.js", f"Here is the file:\n```js\n{JS_FILE}\n```\nRun it:\n```bash\nnode out.js --trace-warnings\n```", JS_FILE),
    # usage first, file second
    ("out.py", f"Install it first:\n```bash\npip install rich\n```\nThen the file:\n```python\n{FILE}\n```", FILE),
    ("out.js", f"First:\n```bash\nnpm install left-pad\n```\nThen:\n```javascript\n{JS_FILE}\n```", JS_FILE),
    # an untagged block is the file before a block tagged as another language, in either order
    ("out.py", f"Run this first:\n```bash\npip install rich\n```\nThe file:\n```\n{FILE}\n```", FILE),
    ("out.js", f"```\n{JS_FILE}\n```\nRun it:\n```sh\nnode out.js --trace-warnings\n```", JS_FILE),
    # two blocks in the file's language: the first is the file
    ("out.py", f"```python\n{FILE}\n```\nA longer variant:\n```python\nprint('hi')\nprint('again')\n```", FILE),
    ("out.js", f"```js\n{JS_FILE}\n```\nOr:\n```js\nconsole.log(2); console.log(3)\n```", JS_FILE),
    ("run.sh", "```bash\necho hi\n```\nRun it:\n```bash\nchmod +x run.sh && ./run.sh\n```", "echo hi"),
    # ...unless, for Python and JSON, the first does not parse and a later one does
    ("out.py", f"```\npip install rich\n```\n```\n{FILE}\n```", FILE),
]

PYTHON_WITH_EXAMPLE = 'def f():\n    return 1\n\n\nDOC = """\n```python\nf()\n```\n"""'
NESTED_EXAMPLE = 'DOC = """\n```bash\nrun me\n```\n"""\nprint(DOC)'
HEREDOC_SCRIPT = "#!/bin/bash\ncat > README.md <<'EOF'\n# Title\n```bash\nnpm i\n```\nEOF\necho done"
README = "# App\n\nInstall:\n```bash\npip install app\n```"

# (output file, model reply, what must be saved). The fences here belong to
# the file and stay in it.
FILE_REPLIES = [
    ("out.py", PYTHON_WITH_EXAMPLE, PYTHON_WITH_EXAMPLE),
    ("out.py", f"```python\n{NESTED_EXAMPLE}\n```", NESTED_EXAMPLE),
    ("mk.sh", HEREDOC_SCRIPT, HEREDOC_SCRIPT),
    ("README.md", README, README),
    ("README.md", f"```markdown\n{README}\n```", README),
]

NO_CODE_REPLIES = ["```python\n```", "Here you go:\n```\n```", "No code, sorry\n```\n\n```", "  \n"]


@pytest.mark.parametrize("filename,reply,expected", CHAT_REPLIES + TWO_BLOCK_REPLIES + FILE_REPLIES)
def test_the_file_is_saved_without_the_chat_around_it(llm, tmp_path, filename, reply, expected):
    llm(StubLLM(reply))
    result, written = _generate(tmp_path, filename=filename)
    assert result.success, result.error
    assert written.read_text() == expected
    assert result.output["syntax_ok"] is (True if filename.endswith((".py", ".json")) else None)


@pytest.mark.parametrize("reply", NO_CODE_REPLIES)
def test_a_reply_with_no_code_is_an_error_and_writes_nothing(llm, tmp_path, reply):
    llm(StubLLM(reply))
    result, written = _generate(tmp_path)
    assert not result.success
    assert "no code" in result.error
    assert not written.exists()


def test_output_that_does_not_parse_is_flagged(llm, tmp_path):
    llm(StubLLM("def broken(:\n    pass"))
    result, written = _generate(tmp_path)
    assert result.success and written.exists()
    assert result.output["syntax_ok"] is False


def test_mcp_clients_get_the_output_path_relative_to_the_checkout(llm, tmp_path, monkeypatch):
    """The chat keeps the real path: it builds the reply's file card from it."""
    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
    llm(StubLLM(FILE))
    over_mcp = ct.CodeGeneratorTool()
    over_mcp.set_context({"transport": "mcp"})
    result = over_mcp.execute(output_filename="p.py", instructions="write a greeting script")
    assert result.output["output_path"] == "outputs/code/p.py"

    in_chat = ct.CodeGeneratorTool().execute(output_filename="p.py", instructions="write a greeting script")
    assert os.path.isabs(in_chat.output["output_path"])
    assert os.path.samefile(in_chat.output["output_path"], tmp_path / "outputs" / "code" / "p.py")


# --- input size ---------------------------------------------------------

def test_an_input_too_long_for_the_context_window_is_refused_before_the_model_runs(llm, tmp_path):
    (tmp_path / "uploads" / "big.py").write_text("x = 1\n" * 40_000)
    stub = llm(StubLLM("x = 2", context_window=8192))
    result, written = _generate(tmp_path, filename="big_v2.py", input_file="big.py", instructions="rename x to y")
    assert not result.success
    assert "too long to rewrite" in result.error and "8,192-token" in result.error
    assert stub.prompts == [] and not written.exists()


def test_an_input_that_fits_is_sent_whole(llm, tmp_path):
    source = "x = 1\n" * 100
    (tmp_path / "uploads" / "small.py").write_text(source)
    stub = llm(StubLLM("y = 1", context_window=8192))
    result, written = _generate(tmp_path, filename="small_v2.py", input_file="small.py", instructions="rename x to y")
    assert result.success, result.error
    assert source in stub.prompts[0]
    assert written.read_text() == "y = 1"


def test_a_client_that_names_no_context_window_is_not_second_guessed(llm, tmp_path):
    (tmp_path / "uploads" / "big.py").write_text("x = 1\n" * 40_000)
    stub = llm(StubLLM("y = 1", context_window=0))
    result, _written = _generate(tmp_path, filename="big_v2.py", input_file="big.py", instructions="rename x to y")
    assert result.success and len(stub.prompts) == 1


def test_an_upload_over_the_byte_cap_is_not_read(llm, tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "MAX_INPUT_BYTES", 1000)
    (tmp_path / "uploads" / "huge.py").write_text("x = 1\n" * 500)
    content, error, found = ct._read_code_input(ct.CodeGeneratorTool(), "huge.py")
    assert content is None and found is None
    assert "larger than" in error


# --- requests aimed at an existing file ---------------------------------

EXISTING = {"README.md", "start.sh", "docker-compose.yml", "backend/app.py", "quality_gate.py"}


@pytest.fixture
def guard(monkeypatch):
    monkeypatch.setattr(ct.CodeGeneratorTool, "_resolves", lambda self, name: name in EXISTING)
    return ct.CodeGeneratorTool()


# (output_filename, instructions): new files, although a name in them exists.
NEW_FILE_REQUESTS = [
    ("README.md", "Generate a README.md for a to-do list web app"),
    ("start.sh", "a bash script that starts my node server"),
    ("app/config.yml", "config similar in spirit to docker-compose.yml but for my app"),
    ("helper.py", "Write a brand new helper.py with a greet function"),
    ("backend/app.py", "a small Flask app with one health route"),
]
# (output_filename, instructions, the file the request is aimed at).
MODIFY_REQUESTS = [
    ("README.md", "Improve the README", "README.md"),
    ("app_v2.py", "Refactor backend/app.py for readability", "backend/app.py"),
    ("backend/app.py", "refactor it", "backend/app.py"),
    ("gate_v2.py", "Improve the uploaded quality_gate.py with better structure", "quality_gate.py"),
    ("out.py", "Improve this file so it runs faster", "out.py"),
]


@pytest.mark.parametrize("output,instructions", NEW_FILE_REQUESTS)
def test_a_new_file_that_shares_an_existing_name_is_allowed(guard, output, instructions):
    assert guard._referenced_existing_file(instructions, output) is None


@pytest.mark.parametrize("output,instructions,expected", MODIFY_REQUESTS)
def test_a_request_to_change_an_existing_file_needs_input_file(guard, output, instructions, expected):
    assert guard._referenced_existing_file(instructions, output) == expected


def test_the_refusal_offers_both_ways_out_and_runs_no_model(llm, guard, tmp_path):
    stub = llm(StubLLM("x = 1"))
    result = guard.execute(output_filename="app_v2.py", instructions="Refactor backend/app.py for readability")
    assert not result.success
    assert "input_file='backend/app.py'" in result.error
    assert "new file" in result.error
    assert stub.prompts == []


def test_a_readme_for_a_new_project_is_written(llm, guard, tmp_path):
    llm(StubLLM("# Todo\n"))
    result = guard.execute(output_filename="README.md", instructions="Generate a README.md for a to-do list web app")
    assert result.success, result.error
    assert (tmp_path / "outputs" / "code" / "README.md").read_text() == "# Todo"


# --- which model a call uses, and what MCP clients are told ---------------

def test_each_call_uses_the_model_that_is_active_now(llm, tmp_path):
    """A tool instance lives as long as the MCP server; the active model can
    change under it."""
    (tmp_path / "uploads" / "mod.py").write_text("x = 1\n")
    generator, reviewer = ct.CodeGeneratorTool(), ct.CodeAnalysisTool()

    first = llm(StubLLM("x = 1", model="model-a"))
    assert generator.execute(output_filename="a.py", instructions="write a greeting script").success
    assert reviewer.execute(file_path="mod.py").success
    assert len(first.prompts) == 2

    second = llm(StubLLM("x = 1", model="model-b"))
    assert generator.execute(output_filename="b.py", instructions="write a greeting script").success
    assert reviewer.execute(file_path="mod.py").success
    assert len(first.prompts) == 2 and len(second.prompts) == 2


def test_declared_hints_say_what_each_tool_changes():
    """MCP clients read these as readOnlyHint and destructiveHint: analyze_code
    writes nothing, however long the model runs; codegen replaces a file of the
    same name in the outputs folder."""
    reviewer, generator = ct.CodeAnalysisTool(), ct.CodeGeneratorTool()
    assert reviewer.read_only is True
    assert (generator.read_only, generator.destructive) == (False, True)
