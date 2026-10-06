"""`--dry-run` on the generation commands must show the request and send no write.

The point of these tests is the negative: `fake_backend.posted_paths()` must be empty
after a dry run, for every command. A dry run that leaked its POST would still print a
plausible body and pass a weaker assertion, so the write check is the contract.
"""
from __future__ import annotations

import json

import pytest

from llx.main import app

# (id, argv, method, path)
CASES = [
    ("images generate", ["images", "generate", "a red fox"], "POST", "/api/batch-image/generate/prompts"),
    ("generate image", ["generate", "image", "a cat"], "POST", "/api/batch-image/generate/prompts"),
    ("videos generate", ["videos", "generate", "a kite over a grey sea"],
     "POST", "/api/batch-video/generate/text"),
    ("music-video create", ["music-video", "create", "--song", "20", "--style", "neon noir"],
     "POST", "/api/music-video"),
    ("film-crew create", ["film-crew", "create", "--script", "INT. ROOM - DAY\nHello."],
     "POST", "/api/production"),
    ("audio music", ["audio", "music", "lo-fi piano"], "POST", "/api/audio-foundry/generate/music"),
    ("audio sfx", ["audio", "sfx", "rain on a tin roof"], "POST", "/api/audio-foundry/generate/fx"),
    ("audio tts", ["audio", "tts", "hello world"], "POST", "/api/audio-foundry/generate/voice"),
    ("infographic generate", ["infographic", "generate", "--scene", "five facts about bees"],
     "POST", "/api/infographic/generate"),
]


@pytest.mark.parametrize("_id,argv,method,path", CASES, ids=[c[0] for c in CASES])
def test_dry_run_shows_the_request_and_sends_no_write(_id, argv, method, path,
                                                       fake_backend, cli_runner, isolated_home):
    fake_backend.default(status=200, json={})  # let read-only resolution succeed

    result = cli_runner.invoke(app, [*argv, "--dry-run", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "dry-run"
    assert payload["command"] == _id
    assert payload["method"] == method
    assert payload["path"] == path
    assert payload["body"] is not None or payload["upload"] is not None
    assert fake_backend.posted_paths() == [], (
        f"{_id}: --dry-run wrote to {fake_backend.posted_paths()}"
    )


def test_dry_run_on_videos_from_image(fake_backend, cli_runner, isolated_home, tmp_path):
    image = tmp_path / "frame.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    fake_backend.default(status=200, json={})

    result = cli_runner.invoke(
        app, ["videos", "from-image", str(image), "--dry-run", "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["method"] == "POST"
    assert payload["path"] == "/api/batch-video/generate/image"
    assert payload["body"]["image_paths"] == [str(image)]
    assert fake_backend.posted_paths() == []


def test_dry_run_provenance_marks_explicit_and_default(fake_backend, cli_runner, isolated_home):
    fake_backend.default(status=200, json={})

    result = cli_runner.invoke(
        app, ["videos", "generate", "a kite", "--seed", "42", "--dry-run", "--json"]
    )

    payload = json.loads(result.output)
    assert payload["inputs"]["prompts"]["source"] == "explicit"
    assert payload["settings"]["seed"]["source"] == "explicit"
    assert payload["settings"]["guidance_scale"]["source"] == "command default"


def test_without_dry_run_the_command_still_writes(fake_backend, cli_runner, isolated_home):
    fake_backend.default(status=200, json={"job_id": "j1"})

    result = cli_runner.invoke(app, ["images", "generate", "a red fox", "--json"])

    assert result.exit_code == 0, result.output
    assert fake_backend.posted_paths() == ["/api/batch-image/generate/prompts"]


# The remaining generation/render commands live in fork-owned modules and gained
# `--dry-run` directly. Same negative contract: no write.
EXTRA_CASES = [
    ("cast generate", ["cast", "generate", "1", "--count", "16"],
     "POST", "/api/cast-library/subjects/1/generate"),
    ("upscale video", ["upscale", "video", "/tmp/x.mp4"],
     "POST", "/api/upscaling/upscale/video"),
    ("upscale image", ["upscale", "image", "/tmp/a.png"],
     "POST", "/api/upscaling/upscale/images"),
    ("video-editor render", ["video-editor", "render", "--audio", "/a.wav", "--video", "/b.mp4"],
     "POST", "/api/video-editor/beat-sync/render"),
    ("video-editor captions-burn", ["video-editor", "captions-burn", "17", "--srt", "/tmp/x.srt"],
     "POST", "/api/video-editor/captions/import"),
    ("generate csv", ["generate", "csv", "a list of five fruits"],
     "POST", "/api/generate/csv"),
    ("videos combine", ["videos", "combine", "Batch_1"],
     "POST", "/api/batch-video/combine-frames/Batch_1"),
    ("video-editor analyze", ["video-editor", "analyze", "--audio", "/a.wav"],
     "POST", "/api/video-editor/analyze"),
]


@pytest.mark.parametrize("_id,argv,method,path", EXTRA_CASES, ids=[c[0] for c in EXTRA_CASES])
def test_extra_generation_dry_runs_send_no_write(_id, argv, method, path,
                                                 fake_backend, cli_runner, isolated_home):
    fake_backend.default(status=200, json={})

    result = cli_runner.invoke(app, [*argv, "--dry-run", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "dry-run"
    assert payload["method"] == method and payload["path"] == path
    assert payload["body"] is not None or payload["upload"] is not None
    assert fake_backend.posted_paths() == [], (
        f"{_id}: --dry-run wrote to {fake_backend.posted_paths()}"
    )


def test_dry_run_human_branch_handles_an_upload(fake_backend, cli_runner, isolated_home,
                                                 monkeypatch, tmp_path):
    """The TTY branch iterates inputs/settings; an upload has no body keys, and getting
    that shape wrong crashed the one branch the pipe-based tests never reach."""
    import llx.output as output

    monkeypatch.setattr(output, "is_pipe", lambda: False)
    fake_backend.default(status=200, json={})
    song = tmp_path / "hook.mp3"
    song.write_bytes(b"ID3")

    result = cli_runner.invoke(app, ["music-video", "create", "--song", str(song),
                                     "--style", "x", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output
    assert "Request body" in result.output
    assert fake_backend.posted_paths() == []


def test_dry_run_human_branch_handles_a_multipart_upload(fake_backend, cli_runner,
                                                          isolated_home, monkeypatch):
    import llx.output as output

    monkeypatch.setattr(output, "is_pipe", lambda: False)
    fake_backend.default(status=200, json={})

    result = cli_runner.invoke(app, ["upscale", "image", "/tmp/a.png", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output
    assert fake_backend.posted_paths() == []


def test_dry_run_json_has_no_garbage_for_an_upload(fake_backend, cli_runner,
                                                   isolated_home, tmp_path):
    """A file-path `--song` is two writes: the upload, then the create. The create inputs
    and settings are client-known and must be shown; only the returned document id is a
    placeholder. Hiding them was the bug this test used to pin."""
    fake_backend.default(status=200, json={})
    song = tmp_path / "hook.mp3"
    song.write_bytes(b"ID3")

    result = cli_runner.invoke(app, ["music-video", "create", "--song", str(song),
                                     "--style", "neon noir", "--model", "wan22-5b",
                                     "--cast", "1", "--lora-consistency", "--dry-run", "--json"])

    payload = json.loads(result.output)
    assert payload["upload"]["path"] == "/api/files/upload"
    assert payload["upload"]["file"].endswith("hook.mp3")
    assert payload["path"] == "/api/music-video"
    assert payload["body"]["style_prompt"] == "neon noir"
    assert payload["body"]["settings"]["subject_ids"] == [1]
    assert payload["body"]["settings"]["i2v_model"] == "wan22-5b"
    assert "document id" in payload["body"]["song_document_id"]
    assert fake_backend.posted_paths() == []
