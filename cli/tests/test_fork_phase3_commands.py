"""Phase 3: video-editor and training-dataset groups.

The render path is the interesting one — it is the first command that submits GPU work
with a payload the CLI builds itself, so the tests pin the exact body it sends, and that
it refuses to guess when the input is missing.
"""
from __future__ import annotations

import json

import pytest

from llx.main import app


def _run(cli_runner, args):
    result = cli_runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# --- video-editor ----------------------------------------------------------


def test_video_editor_projects(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/video-editor/projects", json={
        "projects": [{"id": "abc123", "name": "Last Spark cut", "isDirty": False}],
    })

    payload = _run(cli_runner, ["video-editor", "projects", "--json"])

    assert payload["data"]["projects"][0]["name"] == "Last Spark cut"


def test_video_editor_render_builds_the_beat_sync_body(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/video-editor/beat-sync/render", json={
        "success": True, "data": {"job_id": "r1"},
    })

    payload = _run(cli_runner, [
        "video-editor", "render",
        "--audio", "/data/song.wav",
        "--video", "/data/a.mp4", "--video", "/data/b.mp4",
        "--subdivision", "4", "--tightness", "80", "--seed", "7",
        "--json",
    ])

    assert payload["data"]["job_id"] == "r1"
    body = json.loads(fake_backend.calls_for("POST", "/api/video-editor/beat-sync/render")[0][2])
    assert body["audio_path"] == "/data/song.wav"
    assert body["video_paths"] == ["/data/a.mp4", "/data/b.mp4"]
    assert body["subdivision"] == 4
    assert body["tightness"] == 80
    assert body["seed"] == 7
    assert body["render_mp4"] is True


def test_video_editor_render_needs_audio_and_a_clip(fake_backend, cli_runner, isolated_home):
    """No silent default: a render with no clips is a wasted GPU job."""
    result = cli_runner.invoke(app, ["video-editor", "render", "--json"])

    assert result.exit_code == 2
    assert "MISSING_INPUT" in (result.output or "")
    assert fake_backend.calls == []


def test_video_editor_render_from_file_posts_it_verbatim(fake_backend, cli_runner, isolated_home, tmp_path):
    payload_file = tmp_path / "render.json"
    payload_file.write_text(json.dumps({
        "audio_path": "/data/song.wav",
        "video_paths": ["/data/a.mp4"],
        "style_recipe_name": "Music Video",
    }), encoding="utf-8")
    fake_backend.route("POST", "/api/video-editor/beat-sync/render", json={"success": True, "data": {}})

    _run(cli_runner, ["video-editor", "render", "--from-file", str(payload_file), "--json"])

    body = json.loads(fake_backend.calls_for("POST", "/api/video-editor/beat-sync/render")[0][2])
    assert body["style_recipe_name"] == "Music Video"
    assert body["render_mp4"] is True  # default folded in, the rest untouched


def test_video_editor_render_rejects_a_bad_payload_file(fake_backend, cli_runner, isolated_home, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")

    result = cli_runner.invoke(app, ["video-editor", "render", "--from-file", str(bad), "--json"])

    assert result.exit_code == 1
    assert "not valid JSON" in (result.output or "")
    assert fake_backend.calls == []


def test_video_editor_project_delete_refuses_without_yes(fake_backend, cli_runner, isolated_home):
    result = cli_runner.invoke(app, ["video-editor", "project-delete", "abc", "--json"])

    assert result.exit_code == 2
    assert fake_backend.calls == []


def test_video_editor_shotcut_opens_the_mlt(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/video-editor/open-in-shotcut", json={"success": True, "data": {}})

    _run(cli_runner, ["video-editor", "shotcut", "/data/outputs/videos/mlt-projects/x.mlt", "--json"])

    body = json.loads(fake_backend.calls_for("POST", "/api/video-editor/open-in-shotcut")[0][2])
    assert body == {"mlt_path": "/data/outputs/videos/mlt-projects/x.mlt"}


def test_video_editor_captions_import_needs_a_source(fake_backend, cli_runner, isolated_home):
    result = cli_runner.invoke(app, ["video-editor", "captions-import", "--json"])

    assert result.exit_code == 2
    assert fake_backend.calls == []


# --- training --------------------------------------------------------------


def test_training_datasets(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/training_datasets", json={
        "datasets": [{"id": 2, "name": "Elara refs", "path": "/data/elara", "created_at": "2026-08-01T00:00:00"}],
    })

    payload = _run(cli_runner, ["training", "datasets", "--json"])

    assert payload["data"]["datasets"][0]["name"] == "Elara refs"


def test_training_dataset_new_posts_the_slash_route(fake_backend, cli_runner, isolated_home):
    """The create route is /api/training_datasets/ — with the slash."""
    fake_backend.route("POST", "/api/training_datasets/", json={"id": 3, "name": "New"})

    _run(cli_runner, ["training", "dataset-new", "New", "--path", "/data/new", "--json"])

    calls = fake_backend.calls_for("POST", "/api/training_datasets/")
    assert calls, fake_backend.calls
    assert json.loads(calls[0][2])["name"] == "New"


def test_training_dataset_update_needs_something_to_change(fake_backend, cli_runner, isolated_home):
    result = cli_runner.invoke(app, ["training", "dataset-update", "3", "--json"])

    assert result.exit_code == 2
    assert fake_backend.calls == []


def test_training_dataset_delete_refuses_without_yes(fake_backend, cli_runner, isolated_home):
    result = cli_runner.invoke(app, ["training", "dataset-delete", "3", "--json"])

    assert result.exit_code == 2
    assert fake_backend.calls == []


def test_training_backends_filters_the_plugin_list(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/plugins", json={"plugins": [
        {"id": "comfyui", "status": "running", "enabled": True},
        {"id": "lora_trainer", "status": "stopped", "enabled": True},
        {"id": "runpod_lora_trainer", "status": "stopped", "enabled": False},
    ]})

    payload = _run(cli_runner, ["training", "backends", "--json"])

    assert [b["id"] for b in payload["data"]["backends"]] == ["lora_trainer", "runpod_lora_trainer"]


def test_training_start_names_the_real_launcher(cli_runner, isolated_home):
    """`training start` is not a command; it must say where the run lives, not go silent."""
    result = cli_runner.invoke(app, ["training", "start"])

    assert result.exit_code == 2
    assert "cast train" in result.output
    assert "subject" in result.output.lower()


@pytest.mark.parametrize("name", ["start", "train", "launch", "run"])
def test_training_launcher_names_all_point_to_cast_train(name, cli_runner, isolated_home):
    result = cli_runner.invoke(app, ["training", name])

    assert result.exit_code == 2
    assert "cast train" in result.output


def test_training_help_still_lists_the_dataset_commands(cli_runner, isolated_home):
    """The custom group must not disturb the real subcommands or their help."""
    result = cli_runner.invoke(app, ["training", "--help"])

    assert result.exit_code == 0
    assert "datasets" in result.output
    assert "backends" in result.output


def test_training_guard_is_silent_under_resilient_parsing():
    """Shell completion resolves with resilient_parsing=True; it must not raise."""
    from typer.main import get_command

    from llx.commands._fork import training

    group = get_command(training.app)
    ctx = group.make_context("training", ["start"], resilient_parsing=True)

    assert group.resolve_command(ctx, ["start"]) == (None, None, [])


def test_training_unrelated_typo_still_suggests(cli_runner, isolated_home):
    """The launcher hint must not swallow Typer's normal near-miss suggestions."""
    result = cli_runner.invoke(app, ["training", "backends2"])

    assert result.exit_code == 2
    assert "backends" in result.output
