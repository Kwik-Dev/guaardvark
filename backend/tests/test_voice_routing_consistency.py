"""A named built-in voice is spoken in that voice by every caller.

Audio Foundry's auto mode tried Chatterbox first, and Chatterbox has no
built-in voices, so a Kokoro voice id sent by the Studio or the Film Crew
Editor was dropped without a word. The voice API's streaming path sent Piper's
'libritts' as a Kokoro voice, and the Casting Director read a "voices" key
that /voices never returns. No GPU, network or Audio Foundry process here.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "audio_foundry"


@pytest.fixture
def plugin_import(monkeypatch):
    """Import Audio Foundry's own modules (``backends.*``) the way its service
    does, and put back whatever was loaded before."""
    monkeypatch.syspath_prepend(str(PLUGIN))
    saved = {k: v for k, v in sys.modules.items() if k == "backends" or k.startswith("backends.")}
    for key in saved:
        del sys.modules[key]
    yield importlib.import_module
    for key in [k for k in sys.modules if k == "backends" or k.startswith("backends.")]:
        del sys.modules[key]
    sys.modules.update(saved)


# ---- the rule, in the plugin ---------------------------------------------------------
@pytest.mark.parametrize("params,engine", [
    ({"backend": "auto"}, "auto"),
    ({}, "auto"),
    ({"backend": "auto", "voice_id": "bm_george"}, "kokoro"),
    ({"backend": "auto", "reference_clip_path": "/r/me.wav"}, "chatterbox"),
    # A clip decides the voice; the Studio sends one or the other.
    ({"backend": "auto", "reference_clip_path": "/r/me.wav", "voice_id": "af_heart"}, "chatterbox"),
    ({"backend": "kokoro"}, "kokoro"),
    ({"backend": "chatterbox"}, "chatterbox"),
])
def test_auto_routes_by_what_the_request_names(plugin_import, params, engine):
    assert plugin_import("backends.voice_gen").route(params) == engine


def test_a_named_voice_is_never_swapped_for_chatterbox(plugin_import, tmp_path):
    voice_gen = plugin_import("backends.voice_gen")
    from backends.base import AudioBackend, GenerationResult

    class _Inner(AudioBackend):
        def __init__(self, name, error=None):
            self.name, self.vram_mb_estimate, self.error, self.seen = name, 1, error, []

        @property
        def is_loaded(self):
            return True

        def load(self):
            pass

        def unload(self):
            pass

        def generate(self, **params):
            self.seen.append(params)
            if self.error:
                raise self.error
            return GenerationResult(path=tmp_path / "x.wav", duration_s=1.0, sample_rate=24000,
                                    meta={"backend": self.name, "voice": params.get("voice_id")})

    vg = voice_gen.VoiceGenBackend(output_root=tmp_path)
    vg._chatterbox, vg._kokoro = _Inner("chatterbox"), _Inner("kokoro")
    result = vg.generate(text="hi", backend="auto", voice_id="bm_george")
    assert result.meta == {"backend": "kokoro", "voice": "bm_george"}
    assert vg._chatterbox.seen == []

    vg._kokoro.error = RuntimeError("voice pack not installed")
    with pytest.raises(RuntimeError, match="voice pack"):
        vg.generate(text="hi", backend="auto", voice_id="bm_george")
    assert vg._chatterbox.seen == []

    # A request that names nothing sounds as before: Chatterbox's stock voice.
    vg._kokoro.error = None
    assert vg.generate(text="hi", backend="auto").meta["backend"] == "chatterbox"


# ---- the Film Crew Editor --------------------------------------------------------------
@pytest.mark.parametrize("voice,sent", [
    ("bm_george", "bm_george"),
    ("default", None),
    (None, None),
    ("libritts", None),        # a Piper id typed on the Cast page
    ("af_x.pt", None),
])
def test_the_editor_sends_only_catalog_voices(monkeypatch, voice, sent):
    from backend.services.swarm import clients

    captured = {}

    def fake_generate(self, path, payload, output_path):
        captured.update(payload)
        return output_path

    monkeypatch.setattr(clients.AudioFoundryClient, "_generate", fake_generate)
    clients.AudioFoundryClient("http://127.0.0.1:1").tts(text="Line.", voice=voice, output_path="/x/vo.wav")
    assert captured["backend"] == "auto"
    assert captured.get("voice_id") == sent


# ---- the voice API (chat read-aloud) ---------------------------------------------------
def test_piper_voice_names_are_not_sent_as_kokoro_voices():
    from backend.api import voice_api

    assert voice_api._kokoro_voice_for("libritts") is None
    assert voice_api._kokoro_voice_for("ryan") is None
    assert voice_api._kokoro_voice_for("af_bella") == "af_bella"
    assert voice_api._kokoro_voice_for(None) is None


def test_streamed_speech_renders_once_with_a_voice_kokoro_has(monkeypatch, tmp_path):
    from backend.api import voice_api

    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
    opened = []
    monkeypatch.setattr(voice_api, "_open_audio_foundry_stream",
                        lambda text, voice_id: opened.append(voice_id) or iter([b"RIFF", b"data"]))

    def no_file_render(*a, **k):
        raise AssertionError("stream mode must not render the whole file first")

    monkeypatch.setattr(voice_api, "_try_audio_foundry_voice", no_file_render)
    app = Flask(__name__)
    app.register_blueprint(voice_api.voice_bp)
    client = app.test_client()

    res = client.post("/api/voice/text-to-speech", json={"text": "Hello there", "voice": "libritts", "stream": True})
    assert res.status_code == 200 and res.mimetype == "audio/wav" and res.data == b"RIFFdata"
    res = client.post("/api/voice/text-to-speech", json={"text": "Hello there", "voice": "af_bella", "stream": True})
    assert res.data == b"RIFFdata"
    assert opened == [None, "af_bella"]


def test_a_refused_stream_is_not_played_as_audio(monkeypatch):
    from backend.api import voice_api

    class _Resp:
        def __init__(self, status):
            self.status_code, self.text, self.closed = status, '{"detail": "unknown voice"}', False

        def close(self):
            self.closed = True

        def iter_content(self, chunk_size):
            yield b"RIFF"

    sent = []
    answers = iter([_Resp(400), _Resp(200)])
    monkeypatch.setattr(voice_api.requests, "post",
                        lambda url, json, **k: sent.append(json) or next(answers))
    assert voice_api._open_audio_foundry_stream("hi", None) is None
    assert "voice_id" not in sent[0] and sent[0]["backend"] == "kokoro"
    assert list(voice_api._open_audio_foundry_stream("hi", "af_heart")) == [b"RIFF"]
    assert sent[1]["voice_id"] == "af_heart"


# ---- the Casting Director's choices ----------------------------------------------------
def test_casting_is_offered_the_installed_catalog_voices(monkeypatch):
    from backend.services import audio_foundry_models as afm

    installed = {"voices/af_heart.pt", "voices/bm_george.pt"}
    monkeypatch.setattr(afm, "is_hub_cached", lambda repo, name: name in installed)
    choices = afm.kokoro_voice_choices(installed_only=True)
    assert [c["id"] for c in choices] == ["af_heart", "bm_george"]
    assert all(c["label"] and c["group"] for c in choices)
    assert len(afm.kokoro_voice_choices(installed_only=False)) == len(afm.kokoro_voice_ids())
