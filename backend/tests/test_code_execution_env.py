"""Code run from the editor gets the backend's environment without its secrets."""

import subprocess
from importlib import util
from pathlib import Path

import pytest


_MODULE_PATH = Path(__file__).resolve().parents[1] / "api" / "code_execution_api.py"


@pytest.fixture
def code_execution_api():
    spec = util.spec_from_file_location("code_execution_api_under_test", _MODULE_PATH)
    module = util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


SECRETS = {
    "GUAARDVARK_API_KEY": "the-key",
    "SECRET_KEY": "flask",
    "DATABASE_URL": "postgresql://user:password@localhost/guaardvark",
    "REDIS_URL": "redis://:password@localhost:6379/0",
    "CELERY_BROKER_URL": "redis://localhost:6379/1",
    "HF_TOKEN": "hf_x",
    "DISCORD_BOT_TOKEN": "bot",
    "INTERCONNECTOR_MASTER_PASSWORD": "pw",
    "SOME_SERVICE_CREDENTIALS": "x",
    "SENTRY_DSN": "https://x",
}
KEPT = {
    "PATH": "/usr/bin",
    "HOME": "/home/someone",
    "PYTHONPATH": "/srv/app",
    "VIRTUAL_ENV": "/srv/app/venv",
    "CUDA_VISIBLE_DEVICES": "0",
    "LANG": "C.UTF-8",
}


def test_execution_env_drops_secret_variables_and_keeps_the_rest(code_execution_api, monkeypatch):
    for name, value in {**SECRETS, **KEPT}.items():
        monkeypatch.setenv(name, value)

    env = code_execution_api.execution_env()

    for name in SECRETS:
        assert name not in env, name
    for name, value in KEPT.items():
        assert env[name] == value, name


@pytest.mark.parametrize("runner", ["execute_command", "execute_command_with_stdin"])
def test_editor_code_runs_with_the_scrubbed_environment(code_execution_api, monkeypatch, runner):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "the-key")
    monkeypatch.setenv("HOME", "/home/someone")
    seen = {}

    def fake_run(command, **kwargs):
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(code_execution_api.subprocess, "run", fake_run)

    result = getattr(code_execution_api, runner)(["python3", "-c", "pass"])

    assert result["success"] is True
    assert seen["env"] is not None
    assert "GUAARDVARK_API_KEY" not in seen["env"]
    assert seen["env"]["HOME"] == "/home/someone"
