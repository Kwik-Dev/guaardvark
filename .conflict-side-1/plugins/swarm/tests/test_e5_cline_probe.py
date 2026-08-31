"""E5 — offline backend probes the installed CLI's --help (no model invocation)."""

import pytest

from service.agent_backends import cline_backend
from service.models import SwarmTask


@pytest.fixture(autouse=True)
def clear_probe_cache():
    cline_backend._PROBE_CACHE.clear()
    yield
    cline_backend._PROBE_CACHE.clear()


class _Help:
    def __init__(self, stdout):
        self.stdout = stdout
        self.stderr = ""


def test_probe_parses_prompt_flag(monkeypatch):
    # Fake openclaw advertising --prompt and --model (NOT --message).
    fake_help = "Usage: openclaw [options]\n  --prompt <text>   prompt\n  --model <m>  model\n"

    calls = []

    def fake_run(argv, capture_output=None, text=None, timeout=None):
        calls.append(argv)
        assert argv[-1] == "--help"  # PROBE ONLY
        return _Help(fake_help)

    monkeypatch.setattr(cline_backend.subprocess, "run", fake_run)
    profile = cline_backend.probe_cli("openclaw")

    assert profile["ok"] is True
    assert profile["message_flag"] == "--prompt"
    assert profile["model_flag"] == "--model"
    # cached — second call doesn't re-run
    cline_backend.probe_cli("openclaw")
    assert len(calls) == 1


def test_spawn_uses_prompt_flag_and_no_model_outside_help(monkeypatch, tmp_path):
    """spawn argv uses --prompt; the only place a model could appear is gated by
    the probe profile. The fake help has no --model so the model name must not
    appear anywhere in the launched argv, and the only --help is the probe."""
    fake_help = "Usage: openclaw\n  --prompt <text>   the prompt\n"

    run_calls = []

    def fake_run(argv, **kwargs):
        run_calls.append(argv)
        return _Help(fake_help)

    # resolve_cli_command -> openclaw is "installed"
    monkeypatch.setattr(
        cline_backend.shutil, "which", lambda c: "/usr/bin/openclaw" if c == "openclaw" else None
    )
    monkeypatch.setattr(cline_backend.subprocess, "run", fake_run)

    # Capture the Popen argv and the wrapper script content.
    popen_calls = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            popen_calls["argv"] = argv
            self.pid = 4321

    monkeypatch.setattr(cline_backend.subprocess, "Popen", FakePopen)

    wt = tmp_path / "wt"
    wt.mkdir()
    task = SwarmTask(id="t1", title="Do thing", description="make it so")
    config = {"command": "openclaw", "model": "ollama/some-model:1b", "args": []}

    backend = cline_backend.ClineBackend()
    proc = backend.spawn(str(wt), task, config)

    wrapper = (wt / ".swarm-run.sh").read_text()

    # The model name must NOT appear in the wrapper argv (no --model flag in help).
    assert "ollama/some-model:1b" not in wrapper
    # --prompt is the chosen message flag.
    assert "--prompt" in wrapper
    assert "--message" not in wrapper
    # --help only appeared during the probe (subprocess.run), never in the launched cmd.
    assert all(call[-1] == "--help" for call in run_calls)
    assert "--help" not in wrapper
    assert proc.pid == 4321


def test_spawn_falls_back_when_probe_fails(monkeypatch, tmp_path):
    """Probe failure -> fall back to current shape (--model + --message)."""
    def fake_run(argv, **kwargs):
        raise FileNotFoundError("no such cli")

    monkeypatch.setattr(
        cline_backend.shutil, "which", lambda c: "/usr/bin/cline" if c == "cline" else None
    )
    monkeypatch.setattr(cline_backend.subprocess, "run", fake_run)

    class FakePopen:
        def __init__(self, argv, **kwargs):
            self.pid = 99

    monkeypatch.setattr(cline_backend.subprocess, "Popen", FakePopen)

    wt = tmp_path / "wt"
    wt.mkdir()
    task = SwarmTask(id="t2", title="x", description="y")
    config = {"command": "cline", "model": "ollama/m:1b", "args": []}

    backend = cline_backend.ClineBackend()
    backend.spawn(str(wt), task, config)
    wrapper = (wt / ".swarm-run.sh").read_text()

    assert "--model" in wrapper
    assert "--message" in wrapper


def test_resolve_cli_command_picks_first_installed(monkeypatch):
    monkeypatch.setattr(
        cline_backend.shutil, "which", lambda c: "/usr/bin/openclaw" if c == "openclaw" else None
    )
    assert cline_backend.resolve_cli_command({"command": "cline"}) == "openclaw"

    monkeypatch.setattr(cline_backend.shutil, "which", lambda c: None)
    assert cline_backend.resolve_cli_command({"command": "cline"}) is None
