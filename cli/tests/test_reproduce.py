"""`<group> reproduce <id>` must carry the record through and send nothing by default."""
from __future__ import annotations

import json

from llx.main import app

_IMG_RETRY = {
    "mode": "text", "prompts": ["a red fox"],
    "params": {"model": "zimage-turbo", "style": "cinematic", "steps": 20, "ui_config": {"seed": 3}},
}
_VID_RETRY = {
    "mode": "text", "prompts": ["a kite"],
    "params": {"model": "wan22-5b", "prompt_style": "cinematic", "ui_config": {"tier": "high"}},
}


def test_images_reproduce_carries_every_field_and_sends_nothing(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/batch-image/status/B1",
                       json={"data": {"batch_id": "B1", "retry_data": _IMG_RETRY}})

    result = cli_runner.invoke(app, ["images", "reproduce", "B1", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["sent"] is False
    assert payload["path"] == "/api/batch-image/generate/prompts"
    assert payload["body"]["prompts"] == ["a red fox"]
    assert payload["body"]["steps"] == 20 and payload["body"]["style"] == "cinematic"
    assert payload["body"]["ui_config"] == {"seed": 3}
    assert "ui_config" in payload["inexpressible"]
    assert payload["command_line"].startswith("guaardvark api request POST /api/batch-image/generate/prompts --yes")
    assert fake_backend.posted_paths() == []


def test_videos_reproduce_keeps_params_the_named_command_cannot_express(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/batch-video/status/B2", json={
        "batch_id": "B2", "status": "completed", "total_videos": 1,
        "completed_videos": 1, "failed_videos": 0, "retry_data": _VID_RETRY, "results": []})

    result = cli_runner.invoke(app, ["videos", "reproduce", "B2", "--json"])

    payload = json.loads(result.output)
    assert payload["path"] == "/api/batch-video/generate/text"
    assert payload["body"]["prompts"] == ["a kite"]
    assert payload["body"]["prompt_style"] == "cinematic"
    assert payload["body"]["ui_config"] == {"tier": "high"}
    assert fake_backend.posted_paths() == []


def test_videos_reproduce_image_mode(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/batch-video/status/B3", json={
        "batch_id": "B3", "status": "completed", "total_videos": 1, "completed_videos": 1,
        "failed_videos": 0, "results": [],
        "retry_data": {"mode": "image", "image_paths": ["/tmp/a.png"],
                       "params": {"model": "wan22-5b"}}})

    result = cli_runner.invoke(app, ["videos", "reproduce", "B3", "--json"])

    payload = json.loads(result.output)
    assert payload["path"] == "/api/batch-video/generate/image"
    assert payload["body"]["image_paths"] == ["/tmp/a.png"]


def test_music_video_reproduce_rebuilds_the_create(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/music-video/7", json={
        "id": 7, "name": "Neon", "style_prompt": "neon noir", "song_document_id": 20,
        "user_treatment": "Elara walks.", "settings": {"subject_ids": [1], "max_stretch": 2}})

    result = cli_runner.invoke(app, ["music-video", "reproduce", "7", "--json"])

    payload = json.loads(result.output)
    assert payload["path"] == "/api/music-video"
    assert payload["body"] == {
        "name": "Neon", "song_document_id": 20, "style_prompt": "neon noir",
        "user_treatment": "Elara walks.", "settings": {"subject_ids": [1], "max_stretch": 2}}
    assert payload["inexpressible"] == []
    # Fully expressible: the curated command is preferred over the escape hatch.
    assert payload["named_command_line"].startswith("guaardvark music-video create")
    assert payload["command_line"] == payload["named_command_line"]
    assert fake_backend.posted_paths() == []


def test_film_crew_reproduce_rebuilds_the_create(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/production/3", json={
        "id": 3, "name": "The Last Spark", "script_text": "Title: The Last Spark",
        "project_id": None, "settings_json": {"video_model": "wan22-5b"}})

    result = cli_runner.invoke(app, ["film-crew", "reproduce", "3", "--json"])

    payload = json.loads(result.output)
    assert payload["body"] == {"name": "The Last Spark", "script_text": "Title: The Last Spark",
                               "project_id": None, "settings": {"video_model": "wan22-5b"}}
    assert payload["named_command_line"].startswith("guaardvark film-crew create")
    assert "--model wan22-5b" in payload["named_command_line"]
    assert fake_backend.posted_paths() == []


def test_reproduce_yes_reruns_the_recorded_request(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/batch-image/status/B1",
                       json={"data": {"batch_id": "B1", "retry_data": _IMG_RETRY}})
    fake_backend.route("POST", "/api/batch-image/generate/prompts", json={"batch_id": "B9"})

    result = cli_runner.invoke(app, ["images", "reproduce", "B1", "--yes", "--json"])

    assert result.exit_code == 0, result.output
    calls = fake_backend.calls_for("POST", "/api/batch-image/generate/prompts")
    assert calls, fake_backend.calls
    body = json.loads(calls[0][2])
    assert body["steps"] == 20 and body["ui_config"] == {"seed": 3}


def test_reproduce_redacts_credential_like_keys(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/batch-image/status/B1", json={"data": {
        "batch_id": "B1",
        "retry_data": {"mode": "text", "prompts": ["x"],
                       "params": {"model": "m", "api_key": "sk-do-not-print"}}}})

    result = cli_runner.invoke(app, ["images", "reproduce", "B1", "--json"])

    payload = json.loads(result.output)
    assert "api_key" not in payload["body"]
    assert payload["redacted"] == ["api_key"]
    assert "sk-do-not-print" not in result.output


def test_images_reproduce_has_no_named_command_so_offers_the_api_line(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/batch-image/status/B1",
                       json={"data": {"batch_id": "B1", "retry_data": _IMG_RETRY}})

    result = cli_runner.invoke(app, ["images", "reproduce", "B1", "--json"])

    payload = json.loads(result.output)
    assert payload["named_command_line"] is None
    assert payload["api_command_line"].startswith(
        "guaardvark api request POST /api/batch-image/generate/prompts --yes")
    assert payload["command_line"] == payload["api_command_line"]


def test_reproduce_yes_is_audited_like_api_request(fake_backend, cli_runner, isolated_home):
    from llx.commands._fork import _api_guard as guard

    fake_backend.route("GET", "/api/batch-image/status/B1",
                       json={"data": {"batch_id": "B1", "retry_data": _IMG_RETRY}})
    fake_backend.route("POST", "/api/batch-image/generate/prompts", json={"batch_id": "B9"})

    result = cli_runner.invoke(app, ["images", "reproduce", "B1", "--yes", "--json"])

    assert result.exit_code == 0, result.output
    entries = guard.read_audit(limit=5)
    assert any(e.get("outcome") == "ok" and "/api/batch-image/generate/prompts" in str(e.get("path"))
               for e in entries), entries


def test_music_video_reproduce_refuses_when_the_song_link_is_gone(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/music-video/7", json={
        "id": 7, "name": "Neon", "style_prompt": "neon noir", "song_document_id": None})

    result = cli_runner.invoke(app, ["music-video", "reproduce", "7", "--json"])

    assert result.exit_code == 2, result.output
    assert "song_document_id is null" in result.output
    assert fake_backend.posted_paths() == []


def test_reproduce_without_a_record_exits_2(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/batch-video/status/B9", json={
        "batch_id": "B9", "retry_data": None, "results": []})

    result = cli_runner.invoke(app, ["videos", "reproduce", "B9", "--json"])

    assert result.exit_code == 2, result.output
    assert fake_backend.posted_paths() == []
