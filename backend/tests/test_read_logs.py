"""read_logs serves the log files the product writes, with credentials masked.

The tool runs against a temporary logs folder; the allowlist is checked
against the scripts and modules in the checkout that write each log.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.tests._mcp_sdk import use_mcp_sdk

use_mcp_sdk()  # the adapter imports mcp.types; see backend/tests/_mcp_sdk.py

from backend.mcp.tools_adapter import _tool_input_schema  # noqa: E402
from backend.tools import workstation_tools as wt  # noqa: E402

REPO = Path(wt.__file__).resolve().parents[2]

PASSWORD = "FAKEPASS_" + "probe_123"
API_KEY = "FAKEKEY_" + "probe_abcdef123"
BEARER = "FAKEBEARER." + "probe-0123456789"


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setattr(wt, "_log_dir", lambda: tmp_path)
    (tmp_path / "backend.log").write_text("\n".join([
        f"INFO app : Using database URL: postgresql://guaardvark:{PASSWORD}@localhost:5432/guaardvark",
        f'DEBUG urllib3 : "GET /v1/x?api_key={API_KEY} HTTP/1.1" 200',
        f"DEBUG http : request headers {{'Authorization': 'Bearer {BEARER}'}}",
        "INFO llm : usage prompt_tokens=35 max_tokens=2048 database pool ok",
    ]) + "\n")
    return tmp_path


def _read(**arguments):
    tool = wt.ReadLogsTool()
    tool.set_context({"transport": "mcp"})
    return tool.execute(**arguments)


# ---- redaction --------------------------------------------------------------------

def test_credentials_in_a_log_are_masked(logs):
    result = _read(name="backend.log")

    assert result.success, result.error
    answer = json.dumps(result.output)
    for secret in (PASSWORD, API_KEY, BEARER):
        assert secret not in answer
    text = result.output["text"]
    assert "postgresql://guaardvark:***@localhost:5432/guaardvark" in text
    assert "api_key=***" in text
    assert "'Authorization': 'Bearer ***'" in text
    # Ordinary diagnostics are untouched.
    assert "prompt_tokens=35 max_tokens=2048" in text


@pytest.mark.parametrize("query", [PASSWORD[:9], API_KEY[:12], BEARER[:10]])
def test_a_query_cannot_match_inside_a_masked_value(logs, query):
    result = _read(name="backend.log", query=query)

    assert result.success
    assert result.output["matched_lines"] == 0
    assert result.output["text"] == ""


def test_a_query_still_finds_the_line_and_returns_it_masked(logs):
    result = _read(name="backend.log", query="DATABASE")

    assert result.output["matched_lines"] == 2
    assert PASSWORD not in result.output["text"]
    assert "guaardvark:***@" in result.output["text"]


def test_a_query_with_many_matches_examines_the_newest_and_says_so(logs, monkeypatch):
    monkeypatch.setattr(wt, "_LOG_QUERY_CANDIDATES", 3)
    (logs / "backend.log").write_text("".join(f"line {i} database\n" for i in range(10)))

    result = _read(name="backend.log", query="database")

    assert result.output["matched_lines"] == 3
    assert result.output["text"].splitlines() == ["line 7 database", "line 8 database", "line 9 database"]
    assert "newest 3" in result.output["note"]


# ---- which logs ---------------------------------------------------------------------

@pytest.mark.parametrize("name", ["ollama_serve.log", "discord_bot.log", "celery_beat.log", "backend_startup.log"])
def test_logs_the_product_writes_are_readable(logs, name):
    (logs / name).write_text("one line\n")

    result = _read(name=name)

    assert result.success, result.error
    assert result.output["text"] == "one line"


@pytest.mark.parametrize("name", ["ollama.log", "discord.log", ".env", "backend.log.2026-09-29"])
def test_names_nothing_writes_are_unknown(logs, name):
    (logs / name).write_text("should never be read\n")

    result = _read(name=name)

    assert not result.success
    assert "Unknown log" in result.error


@pytest.mark.parametrize("name", sorted(wt._LOG_NOT_SERVED))
def test_conversation_logs_are_not_served(logs, name):
    (logs / name).write_text("what the user typed\n")

    result = _read(name=name)

    assert not result.success
    assert "not served" in result.error


def test_a_log_that_was_never_written_names_its_writer(logs):
    result = _read(name="comfyui.log")

    assert not result.success
    assert "plugins/comfyui/scripts/start.sh" in result.error


def test_the_names_are_published_as_an_enum():
    schema = _tool_input_schema(wt.ReadLogsTool())

    assert schema["properties"]["name"]["enum"] == sorted(wt._LOG_ALLOWLIST)


def test_served_and_not_served_do_not_overlap():
    assert not set(wt._LOG_ALLOWLIST) & set(wt._LOG_NOT_SERVED)


# ---- the allowlist against the code that writes the logs ---------------------------------

def _celery_worker_logs(text: str) -> set[str]:
    """start_celery.sh names worker logs celery_<worker>.log, one per start_worker call."""
    if "celery_${worker_name}.log" not in text:
        return set()
    return {f"celery_{worker}.log" for worker in re.findall(r'^start_worker "([a-z_]+)"', text, re.MULTILINE)}


@pytest.mark.parametrize("name, writer", sorted(wt._LOG_WRITERS.items()))
def test_every_served_log_has_a_writer_that_names_it(name, writer):
    path = REPO / writer
    assert path.is_file(), f"{writer} (declared writer of {name}) does not exist"
    text = path.read_text(encoding="utf-8", errors="replace")

    assert name in text or name in _celery_worker_logs(text), f"{writer} no longer writes {name}"


def test_every_log_the_start_scripts_write_is_served_or_explicitly_not():
    scripts = [REPO / "start.sh", REPO / "start_celery.sh", *sorted(REPO.glob("plugins/*/scripts/start.sh"))]
    written: dict[str, str] = {}
    for script in scripts:
        text = script.read_text(encoding="utf-8", errors="replace")
        for name in re.findall(r"\$\{?(?:LOGS?_DIR|GUAARDVARK_LOG_DIR)\}?/([A-Za-z0-9_]+\.log)\b", text):
            written.setdefault(name, script.relative_to(REPO).as_posix())
        for name in _celery_worker_logs(text):
            written.setdefault(name, script.relative_to(REPO).as_posix())

    assert len(written) >= 15, "the scan found too few log names; has the scripts' LOG_DIR naming changed?"
    unknown = {name: script for name, script in written.items()
               if name not in wt._LOG_ALLOWLIST and name not in wt._LOG_NOT_SERVED}
    assert not unknown, f"logs written by start scripts but unknown to read_logs: {unknown}"
