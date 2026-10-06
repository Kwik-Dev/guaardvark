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
