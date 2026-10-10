"""Plugin log tail API — maps plugin ids to on-disk log files."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import config
from backend.api import plugins_api


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    path = tmp_path / "logs"
    path.mkdir()
    monkeypatch.setattr(config, "LOG_DIR", str(path))
    return path


def test_video_editor_logs(log_dir):
    (log_dir / "video_editor.log").write_text("beat sync ok\nrender done\n")
    text, count, source = plugins_api._read_plugin_log_text("video_editor", 50)
    assert "beat sync ok" in text
    assert count == 2
    assert source.endswith("video_editor.log")


def test_ollama_reads_serve_log_not_legacy_name(log_dir):
    (log_dir / "ollama_serve.log").write_text("ollama serve listening\n")
    text, count, _source = plugins_api._read_plugin_log_text("ollama", 50)
    assert "ollama serve listening" in text
    assert count == 1


def test_journal_lines_lose_the_hostname_and_keep_the_time(log_dir, monkeypatch):
    """systemd's Ollama has no ollama_serve.log; the journal names the box on every line."""
    journal = (
        "2026-10-10T18:43:33-04:00 somebox ollama[1840771]: [GIN] 2026/10/10 - 18:43:33 | 200 | GET \"/api/tags\"\n"
        "2026-10-10T18:43:34-04:00 somebox ollama[1840771]: time=2026-10-10T18:43:34 level=INFO msg=\"loaded\"\n"
        "-- Boot abc123 --\n"
    )

    def fake_run(argv, **_kw):
        assert argv[:3] == ["journalctl", "-u", "ollama"]
        return SimpleNamespace(returncode=0, stdout=journal)

    monkeypatch.setattr(plugins_api.subprocess, "run", fake_run)

    text, count, source = plugins_api._read_plugin_log_text("ollama", 50)

    assert source == "journalctl:ollama"
    assert count == 3
    assert "somebox" not in text
    assert "ollama[1840771]" not in text
    assert text.splitlines()[0] == "2026-10-10T18:43:33-04:00 [GIN] 2026/10/10 - 18:43:33 | 200 | GET \"/api/tags\""
    assert text.splitlines()[2] == "-- Boot abc123 --"


def test_lora_trainer_reads_the_daemon_log_it_writes(log_dir):
    """real_trainer.py pumps the daemon's stderr into lora_trainer_daemon.log;
    reading lora_trainer.log showed "(no logs)" on the card forever."""
    (log_dir / "lora_trainer_daemon.log").write_text("daemon ready\nstep 10/100\n")
    text, count, source = plugins_api._read_plugin_log_text("lora_trainer", 50)
    assert "step 10/100" in text
    assert count == 2
    assert source.endswith("lora_trainer_daemon.log")


def test_lora_trainer_falls_back_to_the_plain_name(log_dir):
    (log_dir / "lora_trainer.log").write_text("training epoch 1\n")
    text, count, source = plugins_api._read_plugin_log_text("lora_trainer", 50)
    assert "training epoch 1" in text
    assert count == 1
    assert source.endswith("lora_trainer.log")


def test_unknown_plugin_falls_back_to_id_log(log_dir):
    (log_dir / "some_tool.log").write_text("hello from some tool\n")
    text, count, source = plugins_api._read_plugin_log_text("some_tool", 50)
    assert "hello from some tool" in text
    assert count == 1
    assert source.endswith("some_tool.log")