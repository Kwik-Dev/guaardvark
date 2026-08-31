"""Plugin log tail API — maps plugin ids to on-disk log files."""

from pathlib import Path

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


def test_unknown_plugin_falls_back_to_id_log(log_dir):
    (log_dir / "lora_trainer.log").write_text("training epoch 1\n")
    text, count, source = plugins_api._read_plugin_log_text("lora_trainer", 50)
    assert "training epoch 1" in text
    assert count == 1
    assert source.endswith("lora_trainer.log")