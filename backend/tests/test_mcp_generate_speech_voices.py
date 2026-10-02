"""generate_speech: a named voice reaches Kokoro, and only catalog voices are sent.

With engine 'auto' the plugin tries Chatterbox first, and Chatterbox has no
voice ids, so a named voice used to be dropped silently. Voice ids are also
checked here because Kokoro reads a value ending in '.pt' as a file to load
and a comma as a blend. The backend HTTP call is faked; nothing renders.
"""

import pytest

from backend.tools.audio_tools import STUDIO_URL, GenerateSpeechTool


@pytest.fixture
def sent(monkeypatch):
    from backend.utils import backend_http

    calls = []

    def fake_request_json(method, path, payload=None, **kwargs):
        calls.append(payload)
        body = {"path": "/srv/x/voice.wav", "duration_s": 1.2, "document_id": 3,
                "meta": {"backend": payload["backend"], "voice": payload.get("voice_id")}}
        return backend_http.BackendResponse(status=200, body=body, data=body)

    monkeypatch.setattr(backend_http, "request_json", fake_request_json)
    return calls


@pytest.mark.parametrize("engine", [None, "auto", "kokoro"])
def test_a_named_voice_is_spoken_by_kokoro(sent, engine):
    kwargs = {"engine": engine} if engine else {}
    res = GenerateSpeechTool().execute(text="Welcome back", voice="am_michael", **kwargs)
    assert res.success, res.error
    assert sent == [{"text": "Welcome back", "backend": "kokoro", "voice_id": "am_michael",
                     "async": True, "queue": True}]
    assert res.output["engine"] == "kokoro" and res.output["voice"] == "am_michael"


def test_no_voice_keeps_the_engine_choice(sent):
    GenerateSpeechTool().execute(text="hi")
    GenerateSpeechTool().execute(text="hi", engine="chatterbox")
    assert sent == [{"text": "hi", "backend": "auto", "async": True, "queue": True},
                    {"text": "hi", "backend": "chatterbox", "async": True, "queue": True}]


def test_chatterbox_with_a_voice_is_refused(sent):
    res = GenerateSpeechTool().execute(text="hi", voice="af_heart", engine="chatterbox")
    assert res.success is False and "Chatterbox has no built-in voices" in res.error
    assert sent == []


@pytest.mark.parametrize("voice", ["/home/user/anything.pt", "af_heart,af_bella", "xyz",
                                   "AF_HEART", "../voices/af_heart"])
def test_only_catalog_voices_are_sent(sent, voice):
    res = GenerateSpeechTool().execute(text="hi", voice=voice, engine="kokoro")
    assert res.success is False and "Unknown voice" in res.error
    assert "af_heart" in res.error and "bm_george" in res.error  # lists the valid ids
    assert sent == []


def test_description_states_how_voice_and_engine_combine():
    tool = GenerateSpeechTool()
    text = tool.description + tool.parameters["engine"].description + tool.parameters["voice"].description
    assert "always speaks with Kokoro" in tool.description
    assert "Chatterbox has no voice ids" in tool.description
    assert "Manage models" in tool.description
    assert "Kokoro when voice is set" in text


def test_studio_link_points_at_the_audio_page():
    from pathlib import Path

    app_jsx = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "App.jsx").read_text()
    assert STUDIO_URL == "/audio"
    assert f'path="{STUDIO_URL}"' in app_jsx
