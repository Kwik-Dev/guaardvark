"""Phase 2 generation groups: cast, upscale, infographic, and `audio transcribe`.

The first tier that spends GPU, so the tests that matter most here are the ones about
*not* spending it: training and cancellation must refuse without confirmation, and
multipart commands must send the part name each route actually reads.
"""
from __future__ import annotations

import json

from llx.main import app


def _run(cli_runner, args):
    result = cli_runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# --- cast ------------------------------------------------------------------


def test_cast_list_uses_the_collection_route(fake_backend, cli_runner, isolated_home):
    """The collection is GET /api/cast-library. `/subjects` is POST-only and a GET
    there is a 405 — which is exactly what the first version of this command did, and
    what the first version of this test happily asserted, because it mocked the wrong
    path. Only running it against the real backend caught it."""
    fake_backend.route("GET", "/api/cast-library", json={
        "subjects": [{"id": 1, "name": "Elara", "kind": "character", "training_status": "ready"}],
    })

    payload = _run(cli_runner, ["cast", "list", "--json"])

    assert payload["data"]["subjects"][0]["name"] == "Elara"
    assert fake_backend.calls_for("GET", "/api/cast-library")
    assert not fake_backend.calls_for("GET", "/api/cast-library/subjects")


def test_cast_list_filters_by_kind_locally(fake_backend, cli_runner, isolated_home):
    """`?kind=` is accepted and ignored by the backend, so the filter is applied here.
    Passing it through would look like it worked while returning everything."""
    fake_backend.route("GET", "/api/cast-library", json={
        "subjects": [
            {"id": 1, "name": "Elara", "kind": "character"},
            {"id": 5, "name": "Lumin Seed", "kind": "prop"},
        ],
    })

    payload = _run(cli_runner, ["cast", "list", "--kind", "prop", "--json"])

    assert [s["name"] for s in payload["data"]["subjects"]] == ["Lumin Seed"]


def test_cast_approve_sends_the_sample_ids_and_the_flag(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/cast-library/subjects/4/samples/approve", json={"success": True, "data": {}})

    _run(cli_runner, ["cast", "approve", "4", "--sample", "11", "--sample", "12", "--json"])

    body = json.loads(fake_backend.calls_for("POST", "/api/cast-library/subjects/4/samples/approve")[0][2])
    assert body == {"sample_ids": [11, 12], "approved": True}


def test_cast_approve_can_unapprove(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/cast-library/subjects/4/samples/approve", json={"success": True, "data": {}})

    _run(cli_runner, ["cast", "approve", "4", "--sample", "11", "--unapprove", "--json"])

    body = json.loads(fake_backend.calls_for("POST", "/api/cast-library/subjects/4/samples/approve")[0][2])
    assert body["approved"] is False


def test_cast_train_refuses_without_yes_and_touches_nothing(fake_backend, cli_runner, isolated_home):
    """Training runs for hours and can bill for a cloud GPU."""
    result = cli_runner.invoke(app, ["cast", "train", "4", "--json"])

    assert result.exit_code == 2
    assert "CONFIRMATION_REQUIRED" in (result.output or "")
    assert fake_backend.calls == []


def test_cast_train_with_yes_posts(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/cast-library/subjects/4/train", json={"success": True, "data": {}})

    _run(cli_runner, ["cast", "train", "4", "--yes", "--backend", "runpod", "--json"])

    posted = fake_backend.calls_for("POST", "/api/cast-library/subjects/4/train")[0]
    assert json.loads(posted[2])["training_settings"]["backend"] == "runpod"


def test_cast_delete_refuses_without_yes(fake_backend, cli_runner, isolated_home):
    result = cli_runner.invoke(app, ["cast", "delete", "4", "--json"])

    assert result.exit_code == 2
    assert fake_backend.calls == []


def test_cast_import_lora_uploads_under_the_field_the_route_reads(fake_backend, cli_runner, isolated_home, tmp_path):
    """The route reads request.files, and the shared client hardcodes part name \"file\"."""
    weights = tmp_path / "elara.safetensors"
    weights.write_bytes(b"\x00\x01")
    fake_backend.route("POST", "/api/cast-library/subjects/4/import-lora", json={"success": True, "data": {}})

    _run(cli_runner, ["cast", "import-lora", "4", str(weights), "--base", "zimage", "--json"])

    request_body = fake_backend.calls_for("POST", "/api/cast-library/subjects/4/import-lora")[0][2]
    assert b'name="file"' in request_body
    assert b"elara.safetensors" in request_body
    assert b"zimage" in request_body


def test_cast_generate_sends_the_key_the_route_reads(fake_backend, cli_runner, isolated_home):
    """The generate route reads body["n"], not body["count"]. Sending "count" was
    accepted with a 200 and then ignored, so every run used the default of 32."""
    fake_backend.route("POST", "/api/cast-library/subjects/4/generate", json={"success": True, "data": {}})

    _run(cli_runner, ["cast", "generate", "4", "--count", "16", "--json"])

    posted = fake_backend.calls_for("POST", "/api/cast-library/subjects/4/generate")[0]
    assert json.loads(posted[2]) == {"n": 16}


def test_cast_generate_without_count_lets_the_backend_default_apply(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/cast-library/subjects/4/generate", json={"success": True, "data": {}})

    _run(cli_runner, ["cast", "generate", "4", "--json"])

    posted = fake_backend.calls_for("POST", "/api/cast-library/subjects/4/generate")[0]
    assert json.loads(posted[2]) == {}


def test_cast_generate_refuses_a_count_the_route_would_reject(fake_backend, cli_runner, isolated_home):
    """The route only accepts 16 or 32. Refused locally, so the GPU is never queued
    and nothing is sent."""
    fake_backend.default()

    result = cli_runner.invoke(app, ["cast", "generate", "4", "--count", "20", "--json"])

    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["error"]["code"] == "BAD_ARGUMENT"
    assert not fake_backend.calls


# --- upscale ---------------------------------------------------------------


def test_upscale_image_sends_the_files_part(fake_backend, cli_runner, isolated_home, tmp_path):
    """The route reads request.files.getlist(\"files\"); a single part named \"file\" 400s."""
    still = tmp_path / "shot.png"
    still.write_bytes(b"\x89PNG")
    fake_backend.route("POST", "/api/upscaling/upscale/images", json={
        "success": True, "data": {"queued": 1, "rejected": []},
    })

    payload = _run(cli_runner, ["upscale", "image", str(still), "--scale", "2", "--json"])

    assert payload["data"]["queued"] == 1
    body = fake_backend.calls_for("POST", "/api/upscaling/upscale/images")[0][2]
    assert b'name="files"' in body
    assert b"shot.png" in body


def test_upscale_video_posts_the_input_path(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/upscaling/upscale/video", json={"success": True, "data": {"job_id": "j7"}})

    payload = _run(cli_runner, ["upscale", "video", "/data/clip.mp4", "--scale", "2", "--json"])

    assert payload["data"]["job_id"] == "j7"
    body = json.loads(fake_backend.calls_for("POST", "/api/upscaling/upscale/video")[0][2])
    assert body["input_path"] == "/data/clip.mp4"
    assert body["two_pass"] is False  # sent explicitly: the route keeps False, drops None


def test_upscale_cancel_refuses_without_yes(fake_backend, cli_runner, isolated_home):
    result = cli_runner.invoke(app, ["upscale", "cancel", "j7", "--json"])

    assert result.exit_code == 2
    assert fake_backend.calls == []


# --- infographic -----------------------------------------------------------


def test_infographic_generate_needs_a_scene_or_a_prompt(fake_backend, cli_runner, isolated_home):
    result = cli_runner.invoke(app, ["infographic", "generate", "--json"])

    assert result.exit_code == 2
    assert "MISSING_INPUT" in (result.output or "")
    assert fake_backend.calls == []


def test_infographic_generate_posts_the_spec(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/infographic/generate", json={"success": True, "data": {"id": 3}})

    payload = _run(cli_runner, [
        "infographic", "generate", "--scene", "how RAG works",
        "--title", "RAG", "--hashtag", "ai", "--callout", "step one", "--json",
    ])

    assert payload["data"]["id"] == 3
    body = json.loads(fake_backend.calls_for("POST", "/api/infographic/generate")[0][2])
    assert body["scene"] == "how RAG works"
    assert body["hashtags"] == ["ai"]
    assert body["callouts"] == ["step one"]
    assert body["aspect"] == "16:9"


# --- audio transcribe (extension of the upstream audio group) --------------


def test_transcribe_is_registered_on_the_upstream_audio_group():
    """Added from a fork module without editing llx/commands/audio.py."""
    import typer.main as typer_main

    from llx.commands.audio import audio_app

    assert "transcribe" in typer_main.get_command(audio_app).commands


def test_audio_transcribe_uploads_under_the_audio_part(fake_backend, cli_runner, isolated_home, tmp_path):
    clip = tmp_path / "line.wav"
    clip.write_bytes(b"RIFF")
    fake_backend.route("POST", "/api/voice/speech-to-text", json={
        "text": "hello there", "transcribed_text": "hello there",
    })

    payload = _run(cli_runner, ["audio", "transcribe", str(clip), "--json"])

    assert payload["data"]["text"] == "hello there"
    body = fake_backend.calls_for("POST", "/api/voice/speech-to-text")[0][2]
    assert b'name="audio"' in body


def test_audio_transcribe_reports_no_speech(fake_backend, cli_runner, isolated_home, tmp_path):
    clip = tmp_path / "silence.wav"
    clip.write_bytes(b"RIFF")
    fake_backend.route("POST", "/api/voice/speech-to-text", json={"text": ""})

    payload = _run(cli_runner, ["audio", "transcribe", str(clip), "--json"])

    assert payload["data"]["text"] == ""
