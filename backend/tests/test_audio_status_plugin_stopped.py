"""Audio job status when Audio Foundry is stopped, and what a finished audio
job reports.

Polling a song or speech job with the plugin stopped used to say the
Guaardvark backend did not answer and to try again shortly (over MCP), or
that no such job exists (in chat), although the backend answered and only
the plugin was off. A finished job now names the file and its library
document. No network, plugin or database: HTTP calls are faked.
"""

from flask import Flask

from backend.tools import image_tools
from backend.utils import backend_http

JOB = "f" * 32
STOPPED = {"error": "Audio Foundry service not running", "plugin_running": False}


def _status(monkeypatch, answer, transport="mcp"):
    def fake_http_json(method, path, *a, **k):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(image_tools, "_http_json", fake_http_json)
    tool = image_tools.GenerationStatusTool()
    tool.set_context({"transport": transport})
    return tool.execute(batch_id=JOB)


def test_a_stopped_plugin_is_named_not_blamed_on_the_backend(monkeypatch):
    res = _status(monkeypatch, backend_http.BackendError(
        "plugin_offline", "Audio Foundry service not running (start the plugin ...)", status=503, body=STOPPED))
    text = res.output or res.error
    assert "Audio Foundry is not running" in text and "start it in Plugins" in text
    assert "backend did not answer" not in text and "Try again shortly" not in text


def test_an_older_backend_without_the_marker_reads_the_same(monkeypatch):
    res = _status(monkeypatch, backend_http.BackendError(
        "plugin_offline", "Audio Foundry service not running", status=503,
        body={"error": "Audio Foundry service not running"}))
    assert "Audio Foundry is not running" in (res.output or res.error)


def test_a_backend_that_is_down_is_still_reported_as_down(monkeypatch):
    res = _status(monkeypatch, backend_http.BackendError(
        "unreachable", "The Guaardvark backend is not answering at http://127.0.0.1:5000."))
    assert res.success is False and "did not answer" in res.error


def test_in_chat_a_stopped_plugin_is_not_a_missing_job(monkeypatch):
    import requests

    from backend.services import comfyui_music_generator as m3

    monkeypatch.setattr(m3, "job_status", lambda job_id: None)

    def refused(*a, **k):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "get", refused)
    info = image_tools.GenerationStatusTool._audio_status(JOB)
    assert info["status"] == "unknown" and "Audio Foundry is not running" in info["error"]


def test_a_finished_speech_job_names_its_file_and_voice(monkeypatch):
    res = _status(monkeypatch, {
        "id": JOB, "intent": "voice", "status": "done", "progress": {"current": 2, "total": 2},
        "result": {"path": "/srv/x/data/uploads/Audio/v.wav", "duration_s": 3.2, "document_id": 41,
                   "meta": {"backend": "kokoro", "voice": "bm_george"}}})
    assert res.success
    assert "Speech job" in res.output and "complete" in res.output
    assert "Saved as v.wav (library document 41)" in res.output
    assert "Engine: kokoro, voice bm_george" in res.output
    assert "/srv/x" not in res.output


def test_a_file_that_missed_the_library_says_so(monkeypatch):
    res = _status(monkeypatch, {
        "id": JOB, "intent": "music", "status": "done",
        "result": {"path": "/srv/x/data/uploads/Audio/s.wav", "duration_s": 30.0,
                   "registration_error": "registering it with the Guaardvark backend failed"}})
    assert "Saved as s.wav" in res.output
    assert "Saved, but not yet in the library" in res.output


# ---- generate_music / generate_speech error text ---------------------------------------
def test_a_503_from_the_running_plugin_is_reported_in_its_own_words(monkeypatch):
    from backend.tools.audio_tools import GenerateMusicTool

    def fake_request_json(method, path, payload=None, **kwargs):
        raise backend_http.BackendError(
            "plugin_offline", "Music generation needs a CUDA GPU (start the plugin ...)", status=503,
            body={"detail": "Music generation needs a CUDA GPU"})

    monkeypatch.setattr(backend_http, "request_json", fake_request_json)
    res = GenerateMusicTool().execute(style="jazz")
    assert res.success is False and res.error == "Music generation needs a CUDA GPU"


# ---- the proxy's own 503 -----------------------------------------------------------------
def test_the_proxy_marks_its_own_503_as_plugin_not_running(monkeypatch):
    import requests

    from backend.api import audio_foundry_api

    def refused(*a, **k):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(audio_foundry_api.requests, "get", refused)
    app = Flask(__name__)
    app.register_blueprint(audio_foundry_api.audio_foundry_bp)
    res = app.test_client().get(f"/api/audio-foundry/jobs/{JOB}")
    assert res.status_code == 503 and res.get_json() == STOPPED
