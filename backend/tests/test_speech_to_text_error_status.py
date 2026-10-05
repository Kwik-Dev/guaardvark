"""A bad upload is a 400, not a 500.

`POST /api/voice/speech-to-text` used to answer a *malformed* audio file with
`500 {"error": "Speech recognition failed: [Errno 1094995529] Invalid data found when
processing input"}` — it fell into the broad handler meant for engine failures, so a
client's unusable file read as a server fault. Found by sending a 12-byte fake WAV through
`guaardvark audio transcribe`.

Both engine paths decode the upload locally before transcribing, so both can tell the
difference:
  * the faster-whisper path decodes in memory;
  * the whisper-server path decodes in memory too, before handing the temp file to the
    remote server — so a bad file is caught here rather than reported as the server's fault.

A genuine engine failure still gets a 500, and the two tests below pin both directions —
a status code that only ever returns 400 would be just as wrong.
"""
from __future__ import annotations

import io

import pytest
from flask import Flask

import backend.api.voice_api as voice_api


@pytest.fixture
def client(monkeypatch):
    """The voice blueprint on a throwaway app, with the rate limiter and the process
    monitor stubbed so the request reaches the audio handling."""
    monkeypatch.setattr(voice_api, "check_rate_limit", lambda request: (True, ""))
    monkeypatch.setattr(
        voice_api, "process_monitor",
        type("M", (), {"get_system_status": staticmethod(lambda: {"system_overloaded": False})})(),
    )
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(voice_api.voice_bp)
    return app.test_client()


def _post(client, payload=b"RIFF0000WAVEnot-really", filename="clip.wav"):
    return client.post(
        "/api/voice/speech-to-text",
        data={"audio": (io.BytesIO(payload), filename)},
        content_type="multipart/form-data",
    )


@pytest.fixture
def undecodable(monkeypatch):
    """Make the local decode fail the way it does for a malformed WAV, without needing
    faster-whisper installed or ffmpeg present."""

    def boom(_stream):
        raise ValueError("[Errno 1094995529] Invalid data found when processing input")

    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", type("m", (), {})())
    monkeypatch.setitem(
        __import__("sys").modules, "faster_whisper.audio",
        type("m", (), {"decode_audio": staticmethod(boom)})(),
    )


def test_a_malformed_upload_is_a_400_not_a_500(client, monkeypatch, undecodable):
    monkeypatch.setattr(voice_api, "USE_WHISPER_SERVER", False)
    monkeypatch.setattr("backend.utils.faster_whisper_utils.FASTER_WHISPER_AVAILABLE", True)

    response = _post(client)

    assert response.status_code == 400, response.get_data(as_text=True)
    assert "decode the uploaded audio" in response.get_json()["error"]


def test_the_whisper_server_path_rejects_a_malformed_upload_the_same_way(
    client, monkeypatch, undecodable
):
    monkeypatch.setattr(voice_api, "USE_WHISPER_SERVER", True)

    response = _post(client)

    assert response.status_code == 400, response.get_data(as_text=True)


def test_an_engine_failure_is_still_a_500(client, monkeypatch):
    """The other direction: a real engine failure must not be dressed up as bad input."""
    monkeypatch.setattr(voice_api, "USE_WHISPER_SERVER", False)
    monkeypatch.setattr("backend.utils.faster_whisper_utils.FASTER_WHISPER_AVAILABLE", True)

    def decode_ok(_stream):
        return [0.0] * 16000

    monkeypatch.setitem(
        __import__("sys").modules, "faster_whisper", type("m", (), {})()
    )
    monkeypatch.setitem(
        __import__("sys").modules, "faster_whisper.audio",
        type("m", (), {"decode_audio": staticmethod(decode_ok)})(),
    )

    def transcribe_boom(*_a, **_k):
        raise RuntimeError("model weights missing")

    monkeypatch.setattr("backend.utils.faster_whisper_utils.transcribe_audio_faster", transcribe_boom)

    response = _post(client)

    assert response.status_code == 500, response.get_data(as_text=True)
    assert "Speech recognition failed" in response.get_json()["error"]
