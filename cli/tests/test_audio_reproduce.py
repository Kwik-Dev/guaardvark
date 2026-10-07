"""`audio jobs` / `audio reproduce` read the recorded generation and replay it (issue #8).

The record comes from the backend's main-DB row; these tests drive the CLI against a
fake backend. The negative contract is the same as the other reproduce commands: the
default emits and sends nothing, and `--yes` goes through the shared audited gate.
"""
from __future__ import annotations

import json

from llx.main import app

_MUSIC_ROW = {
    "id": 4, "kind": "music", "status": "completed", "model": "acestep", "seed": 42,
    "inputs": {"style_prompt": "lo-fi piano", "duration_s": 30.0,
               "instrumental_only": True, "async": True, "model": "acestep", "seed": 42},
    "output_path": "/out/song.wav", "document_id": 9,
}
_VOICE_ROW = {
    "id": 5, "kind": "voice", "status": "completed",
    "inputs": {"text": "Hello.", "voice_id": "af_heart", "backend": "kokoro", "async": True},
}


def _list(fake_backend, rows):
    fake_backend.route("GET", "/api/audio-foundry/generations",
                       json={"success": True, "generations": rows})


def test_audio_jobs_lists_recorded_generations(fake_backend, cli_runner, isolated_home):
    _list(fake_backend, [_MUSIC_ROW, _VOICE_ROW])

    result = cli_runner.invoke(app, ["audio", "jobs", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    kinds = [g["kind"] for g in payload["data"]["generations"]]
    assert kinds == ["music", "voice"]
    assert payload["data"]["generations"][0]["inputs"]["style_prompt"] == "lo-fi piano"


def test_audio_jobs_details_one_generation(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/audio-foundry/generations/4",
                       json={"success": True, "generation": _MUSIC_ROW})

    result = cli_runner.invoke(app, ["audio", "jobs", "4", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["generations"][0]["id"] == 4


def test_audio_reproduce_music_emits_the_recorded_request(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/audio-foundry/generations/4",
                       json={"success": True, "generation": _MUSIC_ROW})

    result = cli_runner.invoke(app, ["audio", "reproduce", "4", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["sent"] is False
    assert payload["path"] == "/api/audio-foundry/generate/music"
    assert payload["body"]["style_prompt"] == "lo-fi piano"
    # model/seed have no `audio music` flag: they are named, and the lossless api line wins.
    assert "model" in payload["inexpressible"] and "seed" in payload["inexpressible"]
    assert payload["named_command_line"] is None
    assert payload["command_line"].startswith(
        "guaardvark api request POST /api/audio-foundry/generate/music --yes")
    assert fake_backend.posted_paths() == []


def test_audio_reproduce_tts_is_fully_expressible(fake_backend, cli_runner, isolated_home):
    row = {"id": 6, "kind": "voice", "status": "completed", "inputs": {"text": "Hello."}}
    fake_backend.route("GET", "/api/audio-foundry/generations/6",
                       json={"success": True, "generation": row})

    result = cli_runner.invoke(app, ["audio", "reproduce", "6", "--json"])

    payload = json.loads(result.output)
    assert payload["inexpressible"] == []
    assert payload["named_command_line"].startswith("guaardvark audio tts")
    assert payload["command_line"] == payload["named_command_line"]


def test_audio_reproduce_names_fields_the_named_tts_cannot_express(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/audio-foundry/generations/5",
                       json={"success": True, "generation": _VOICE_ROW})

    result = cli_runner.invoke(app, ["audio", "reproduce", "5", "--json"])

    payload = json.loads(result.output)
    assert payload["named_command_line"] is None
    assert {"voice_id", "backend"} <= set(payload["inexpressible"])


def test_audio_reproduce_yes_reruns_and_is_audited(fake_backend, cli_runner, isolated_home):
    from llx.commands._fork import _api_guard as guard

    fake_backend.route("GET", "/api/audio-foundry/generations/4",
                       json={"success": True, "generation": _MUSIC_ROW})
    fake_backend.route("POST", "/api/audio-foundry/generate/music", json={"job_id": "j1"})

    result = cli_runner.invoke(app, ["audio", "reproduce", "4", "--yes", "--json"])

    assert result.exit_code == 0, result.output
    calls = fake_backend.calls_for("POST", "/api/audio-foundry/generate/music")
    assert calls, fake_backend.calls
    body = json.loads(calls[0][2])
    assert body["style_prompt"] == "lo-fi piano" and body["seed"] == 42
    entries = guard.read_audit(limit=5)
    assert any(e.get("outcome") == "ok"
               and "/api/audio-foundry/generate/music" in str(e.get("path")) for e in entries), entries


def test_audio_reproduce_without_a_record_exits_2(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/audio-foundry/generations/9",
                       json={"success": True, "generation": {}})

    result = cli_runner.invoke(app, ["audio", "reproduce", "9", "--json"])

    assert result.exit_code == 2, result.output
    assert fake_backend.posted_paths() == []
