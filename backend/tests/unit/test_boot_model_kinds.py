"""start.sh tells chat models from embedding models by what Ollama reports
(/api/show capabilities) or a declared row, not by "embed"/"minilm" in the
name: bge-m3, snowflake-arctic-embed2 and mxbai lack one or the other, and a
chat model named "...embed..." is still a chat model."""
import io
import os
import stat
import subprocess
from pathlib import Path

import pytest

from backend.services import model_capabilities as mc

REPO = Path(__file__).resolve().parents[3]

OLLAMA_LIST = """NAME                     ID              SIZE      MODIFIED
gemma4:e2b               aaaaaaaaaaaa    7.2 GB    2 days ago
bge-m3:latest            bbbbbbbbbbbb    1.2 GB    2 days ago
mxbai-large:latest       cccccccccccc    669 MB    2 days ago
unreachable-embed:v1     dddddddddddd    300 MB    2 days ago
"""

SHOW = {
    "gemma4:e2b": {"capabilities": ["completion", "tools", "vision"]},
    "bge-m3:latest": {"capabilities": ["embedding"]},
    "mxbai-large:latest": {"capabilities": ["embedding"]},
}


@pytest.fixture
def ollama(monkeypatch):
    monkeypatch.setattr(mc, "_info", lambda tag: SHOW.get(tag))
    monkeypatch.setattr(mc, "_local_rows", lambda: {})


def test_kinds_come_from_ollamas_capabilities(ollama):
    assert mc.model_kind("bge-m3:latest") == ("embedding", "api_show")
    assert mc.model_kind("mxbai-large:latest") == ("embedding", "api_show")
    assert mc.model_kind("gemma4:e2b") == ("chat", "api_show")
    # Ollama could not describe it and nothing declares it: the name decides.
    assert mc.model_kind("unreachable-embed:v1") == ("embedding", "name_fallback")


def test_a_declared_row_answers_when_ollama_cannot(ollama, monkeypatch):
    monkeypatch.setattr(mc, "_local_rows", lambda: {"house-vectors:1": {"embedding": True}})
    assert mc.model_kind("house-vectors:1") == ("embedding", "local_row")


def test_the_cli_reads_ollama_list(ollama, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(OLLAMA_LIST))
    assert mc.main(["kinds"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "gemma4:e2b\tchat\tapi_show",
        "bge-m3:latest\tembedding\tapi_show",
        "mxbai-large:latest\tembedding\tapi_show",
        "unreachable-embed:v1\tembedding\tname_fallback",
    ]


def _shell_function(name: str) -> str:
    text = (REPO / "start.sh").read_text()
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


def _run_kinds(tmp_path, python_body: str, **env) -> str:
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    py = venv / "python"
    py.write_text("#!/bin/bash\n" + python_body)
    py.chmod(py.stat().st_mode | stat.S_IEXEC)
    script = _shell_function("ollama_model_kinds") + '\nollama_model_kinds\n'
    return subprocess.run(["bash", "-c", script], input=OLLAMA_LIST, capture_output=True, text=True,
                          cwd=tmp_path, env={**os.environ, "VENV_DIR": str(tmp_path / "venv"), **env},
                          check=True).stdout


def test_start_sh_uses_the_backends_answer(tmp_path):
    out = _run_kinds(tmp_path, 'cat >/dev/null; printf "bge-m3:latest\\tembedding\\tapi_show\\n"\n')
    assert out == "bge-m3:latest\tembedding\tapi_show\n"


def test_start_sh_asks_from_the_checkout_whatever_the_cwd(tmp_path):
    # python -m backend... resolves only from the checkout; start.sh may be run from anywhere.
    out = _run_kinds(tmp_path, 'cat >/dev/null; printf "%s\\tchat\\tapi_show\\n" "$PWD"\n',
                     SCRIPT_DIR=str(REPO))
    assert out == f"{REPO}\tchat\tapi_show\n"


def test_start_sh_falls_back_to_the_name_without_a_venv(tmp_path):
    out = _run_kinds(tmp_path, "exit 1\n")
    assert out.splitlines() == [
        "gemma4:e2b\tchat\tname_fallback",
        "bge-m3:latest\tchat\tname_fallback",
        "mxbai-large:latest\tchat\tname_fallback",
        "unreachable-embed:v1\tembedding\tname_fallback",
    ]
