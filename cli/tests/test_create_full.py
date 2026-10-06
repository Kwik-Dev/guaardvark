"""`create` must accept the full inputs and post them unchanged (issue #8, part 3)."""
from __future__ import annotations

import json

from llx.main import app


def _body(fake_backend, path):
    calls = fake_backend.calls_for("POST", path)
    assert calls, fake_backend.calls
    return json.loads(calls[0][2])


def test_music_video_create_forwards_treatment_and_settings(fake_backend, cli_runner, isolated_home):
    fake_backend.default(status=200, json={"id": 9, "name": "Dup", "current_stage": "analyzing"})

    result = cli_runner.invoke(app, [
        "music-video", "create", "--song", "20", "--style", "neon noir", "--name", "Dup",
        "--treatment", "Elara walks.", "--cast", "1", "--lora-consistency",
        "--planning-mode", "narrative", "--fill-method", "forward",
        "--max-stretch", "2", "--interp", "2", "--model", "wan22-5b", "--json",
    ])

    assert result.exit_code == 0, result.output
    body = _body(fake_backend, "/api/music-video")
    assert body["user_treatment"] == "Elara walks."
    assert body["song_document_id"] == 20
    assert body["settings"] == {
        "i2v_model": "wan22-5b",
        "use_lora_consistency": True,
        "subject_ids": [1],
        "keyframe_model": "from-lora",
        "planning_mode": "narrative",
        "fill_method": "forward",
        "max_stretch": 2.0,
        "interpolation_multiplier": 2,
    }
    # The cast lock must not silently imply an approval.
    assert not any(p.endswith("/approve") for p in fake_backend.posted_paths())


def test_music_video_create_uploads_a_song_path_then_posts(fake_backend, cli_runner, isolated_home, tmp_path):
    song = tmp_path / "hook.mp3"
    song.write_bytes(b"ID3")
    fake_backend.route("POST", "/api/files/upload", json={"id": 77})
    fake_backend.route("POST", "/api/music-video", json={"id": 9, "name": "hook", "current_stage": "analyzing"})

    result = cli_runner.invoke(app, [
        "music-video", "create", "--song", str(song), "--style", "x", "--json"])

    assert result.exit_code == 0, result.output
    body = _body(fake_backend, "/api/music-video")
    assert body["song_document_id"] == 77


def test_film_crew_create_forwards_extra_settings(fake_backend, cli_runner, isolated_home):
    fake_backend.default(status=200, json={"id": 4, "name": "Test", "current_stage": "screenwriting"})

    result = cli_runner.invoke(app, [
        "film-crew", "create", "--script", "INT. ROOM - DAY\nHello.", "--model", "wan22-5b",
        "--settings", '{"image_model": "flux-dev"}', "--json"])

    assert result.exit_code == 0, result.output
    body = _body(fake_backend, "/api/production")
    assert body["settings"] == {"video_model": "wan22-5b", "image_model": "flux-dev"}


def test_film_crew_create_rejects_bad_settings_json(fake_backend, cli_runner, isolated_home):
    fake_backend.default(status=200, json={})

    result = cli_runner.invoke(app, [
        "film-crew", "create", "--script", "INT. X", "--settings", "not json", "--json"])

    assert result.exit_code == 2, result.output
    assert fake_backend.posted_paths() == []


def test_create_dry_run_refuses_without_spending(fake_backend, cli_runner, isolated_home):
    fake_backend.default(status=200, json={})

    result = cli_runner.invoke(app, [
        "music-video", "create", "--song", "20", "--style", "x", "--cast", "1",
        "--lora-consistency", "--dry-run", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["body"]["settings"]["subject_ids"] == [1]
    assert fake_backend.posted_paths() == []
