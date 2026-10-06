"""The human `status` views must show the recorded settings (issue #8, part 2).

`--json` passthrough is pinned by the golden tier; these cover the human branch, which is
the part that used to drop the settings entirely.
"""
from __future__ import annotations

import llx.output as output
from llx.main import app


def _human(monkeypatch):
    """Force the human branch: CliRunner's stdout is a pipe, which would select JSON."""
    monkeypatch.setattr(output, "is_pipe", lambda: False)


def test_images_status_human_shows_recorded_settings(fake_backend, cli_runner, isolated_home, monkeypatch):
    _human(monkeypatch)
    fake_backend.route("GET", "/api/batch-image/status/B1", json={"data": {
        "batch_id": "B1", "status": "completed", "progress": 100,
        "retry_data": {"mode": "text", "prompts": ["a red fox"],
                       "params": {"model": "zimage-turbo", "steps": 9}}}})

    result = cli_runner.invoke(app, ["images", "status", "B1"])

    assert result.exit_code == 0, result.output
    assert "Recorded settings" in result.output
    assert "zimage-turbo" in result.output
    assert "a red fox" in result.output


def test_videos_status_human_warns_when_nothing_recorded(fake_backend, cli_runner, isolated_home, monkeypatch):
    _human(monkeypatch)
    fake_backend.route("GET", "/api/batch-video/status/B2", json={
        "batch_id": "B2", "status": "completed", "total_videos": 1,
        "completed_videos": 1, "failed_videos": 0, "retry_data": None, "results": []})

    result = cli_runner.invoke(app, ["videos", "status", "B2"])

    assert result.exit_code == 0, result.output
    assert "no recorded settings" in result.output


def test_music_video_status_human_shows_cast_and_treatment(fake_backend, cli_runner, isolated_home, monkeypatch):
    _human(monkeypatch)
    fake_backend.route("GET", "/api/music-video/7", json={
        "id": 7, "name": "Neon", "status": "complete", "current_stage": "complete",
        "subject_ids": [1], "user_treatment": "Elara walks the overgrown path.",
        "use_lora_consistency": True, "keyframe_model": "from-lora", "i2v_model": "wan22-5b",
        "settings": {"max_stretch": 2}, "cut_plan": [], "clips": []})

    result = cli_runner.invoke(app, ["music-video", "status", "7"])

    assert result.exit_code == 0, result.output
    assert "Recorded inputs" in result.output
    assert "Elara walks the overgrown path." in result.output
    assert "max_stretch" in result.output


def test_film_crew_status_human_warns_when_settings_empty(fake_backend, cli_runner, isolated_home, monkeypatch):
    _human(monkeypatch)
    fake_backend.route("GET", "/api/production/3", json={
        "id": 3, "name": "The Last Spark", "status": "complete", "current_stage": "complete",
        "shots": [], "script_text": "Title: The Last Spark", "settings_json": {}})

    result = cli_runner.invoke(app, ["film-crew", "status", "3"])

    assert result.exit_code == 0, result.output
    assert "no recorded settings" in result.output
