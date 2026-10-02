"""Voice cloning needs an explicit consent record, checked by the backend proxy
and again by Audio Foundry where the clone happens.

Uploading a clip used to create an empty ``<clip>.consent`` on its own, so every
upload passed the proxy's check, and the plugin cloned any clip it was handed.
Now only the person's confirmation writes a record (JSON with the clip's
SHA-256), and the plugin refuses a clip without one. No GPU, network or
database: the plugin is never called, and Chatterbox's model is a stub.
"""

from __future__ import annotations

import importlib
import io
import json
import os
import sys
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "audio_foundry"


@pytest.fixture
def consent():
    from backend.services.audio_foundry_models import voice_consent
    return voice_consent()


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


def _clip(folder: Path, name: str = "me.wav", data: bytes = b"RIFF0000WAVEfmt voice") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    return path


# ---- the shared rules ----------------------------------------------------------------
def test_an_empty_consent_file_from_an_old_upload_is_not_consent(consent, tmp_path):
    refs = tmp_path / "voice_references"
    clip = _clip(refs)
    (refs / "me.wav.consent").touch()
    assert consent.has_consent(clip) is False
    with pytest.raises(consent.ConsentRequired, match="no consent has been recorded"):
        consent.require_consent(str(clip), refs)


def test_a_confirmed_record_allows_the_clip_until_the_recording_changes(consent, tmp_path):
    refs = tmp_path / "voice_references"
    clip = _clip(refs)
    record = consent.write_record(clip, source="audio_studio")
    assert record["kind"] == "voice_clone" and record["statement"] == consent.STATEMENT
    stored = json.loads((refs / "me.wav.consent").read_text())
    assert stored["sha256"] == consent.sha256_of(clip)
    assert consent.require_consent("me.wav", refs) == clip.resolve()

    clip.write_bytes(b"RIFF0000WAVEfmt someone else")
    with pytest.raises(consent.ConsentRequired, match="changed after consent"):
        consent.require_consent(str(clip), refs)


def test_only_audio_files_inside_voice_references_are_considered(consent, tmp_path):
    refs = tmp_path / "voice_references"
    _clip(refs)
    outside = _clip(tmp_path / "elsewhere", "private.wav")
    consent.write_record(outside, source="audio_studio")
    # A valid record does not make a clip outside the folder usable.
    with pytest.raises(consent.ConsentRequired, match="imported in Audio Studio"):
        consent.require_consent(str(outside), refs)
    with pytest.raises(consent.ConsentRequired, match="imported in Audio Studio"):
        consent.require_consent("../elsewhere/private.wav", refs)
    # Nor does a symlink inside the folder that points out of it.
    os.symlink(outside, refs / "link.wav")
    with pytest.raises(consent.ConsentRequired, match="imported in Audio Studio"):
        consent.require_consent(str(refs / "link.wav"), refs)
    with pytest.raises(consent.ConsentRequired, match="not an audio clip"):
        consent.require_consent(str(_clip(refs, "notes.txt")), refs)
    with pytest.raises(consent.ConsentRequired, match="not in Audio Studio"):
        consent.require_consent(str(refs / "missing.wav"), refs)


def test_a_symlinked_record_is_not_a_record(consent, tmp_path):
    refs = tmp_path / "voice_references"
    a = _clip(refs, "a.wav")
    b = _clip(refs, "b.wav", b"RIFF0000WAVEfmt b")
    consent.write_record(a, source="audio_studio")
    os.symlink(refs / "a.wav.consent", refs / "b.wav.consent")
    assert consent.has_consent(b) is False


def test_the_plugin_finds_uploads_the_way_the_backend_config_does(consent, monkeypatch, tmp_path):
    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
    monkeypatch.delenv("GUAARDVARK_UPLOAD_DIR", raising=False)
    assert consent.references_dir() == tmp_path / "data" / "uploads" / "voice_references"
    monkeypatch.setenv("GUAARDVARK_UPLOAD_DIR", "store/up")
    assert consent.references_dir() == tmp_path / "store" / "up" / "voice_references"
    monkeypatch.setenv("GUAARDVARK_UPLOAD_DIR", str(tmp_path / "abs"))
    assert consent.references_dir() == tmp_path / "abs" / "voice_references"


# ---- the backend routes --------------------------------------------------------------
@pytest.fixture
def studio(monkeypatch, tmp_path):
    from backend.api import audio_foundry_api

    app = Flask(__name__)
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    app.register_blueprint(audio_foundry_api.audio_foundry_bp)
    forwarded = []

    def fake_generate(path, data):
        forwarded.append((path, data))
        return {"path": "/x/out.wav"}, 200

    monkeypatch.setattr(audio_foundry_api, "_proxy_generate", fake_generate)
    return app.test_client(), forwarded, tmp_path / "uploads" / "voice_references"


def _upload(client, name="me.wav", **form):
    data = {"file": (io.BytesIO(b"RIFF0000WAVEfmt voice"), name), "name": name, **form}
    return client.post("/api/audio-foundry/voice-clips/upload", data=data,
                       content_type="multipart/form-data")


def test_an_upload_without_confirmation_cannot_be_cloned(studio):
    client, forwarded, refs = studio
    up = _upload(client)
    assert up.status_code == 201 and up.get_json()["consented"] is False
    assert not (refs / "me.wav.consent").exists()

    res = client.post("/api/audio-foundry/generate/voice",
                      json={"text": "hi", "backend": "chatterbox", "reference_clip_path": up.get_json()["path"]})
    assert res.status_code == 403 and res.get_json()["needs_consent"] is True
    assert forwarded == []


def test_a_confirmed_upload_is_cloned_by_its_checked_path(studio):
    client, forwarded, refs = studio
    up = _upload(client, consent_confirmed="true").get_json()
    assert up["consented"] is True
    record = json.loads((refs / "me.wav.consent").read_text())
    assert record["kind"] == "voice_clone" and record["source"] == "audio_studio"

    res = client.post("/api/audio-foundry/generate/voice",
                      json={"text": "hi", "backend": "chatterbox", "reference_clip_path": "me.wav"})
    assert res.status_code == 200
    assert forwarded[0][1]["reference_clip_path"] == str((refs / "me.wav").resolve())


def test_an_older_clip_is_confirmed_through_its_own_route(studio):
    client, forwarded, refs = studio
    _upload(client)
    (refs / "me.wav.consent").touch()  # what uploads used to leave behind

    listed = client.get("/api/audio-foundry/voice-clips").get_json()
    assert listed["clips"][0]["consented"] is False
    assert "right to clone this voice" in listed["consent_statement"]

    assert client.post("/api/audio-foundry/voice-clips/me/consent", json={}).status_code == 400
    assert client.post("/api/audio-foundry/voice-clips/nope/consent",
                       json={"confirmed": True}).status_code == 404
    ok = client.post("/api/audio-foundry/voice-clips/me/consent", json={"confirmed": True})
    assert ok.status_code == 200 and ok.get_json()["consented"] is True
    assert client.get("/api/audio-foundry/voice-clips").get_json()["clips"][0]["consented"] is True

    res = client.post("/api/audio-foundry/generate/voice",
                      json={"text": "hi", "reference_clip_path": str(refs / "me.wav")})
    assert res.status_code == 200 and len(forwarded) == 1


def test_a_path_outside_voice_references_is_refused(studio, tmp_path):
    client, forwarded, _ = studio
    private = _clip(tmp_path / "home", "private.wav")
    res = client.post("/api/audio-foundry/generate/voice",
                      json={"text": "hi", "reference_clip_path": str(private)})
    assert res.status_code == 403 and forwarded == []


def test_the_clip_download_never_serves_the_consent_record(studio):
    client, _, refs = studio
    _upload(client, consent_confirmed="true")
    res = client.get("/api/audio-foundry/voice-clips/me/download")
    assert res.status_code == 200 and res.data.startswith(b"RIFF")


# ---- the plugin, where the clone happens ---------------------------------------------
def test_chatterbox_refuses_a_clip_without_consent_before_generating(plugin_import, monkeypatch, tmp_path):
    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
    monkeypatch.delenv("GUAARDVARK_UPLOAD_DIR", raising=False)
    chatterbox = plugin_import("backends.voice_gen_chatterbox")
    voice_consent = plugin_import("backends.voice_consent")
    clip = _clip(tmp_path / "data" / "uploads" / "voice_references")

    class _Model:
        calls = 0

        def generate(self, **kwargs):
            _Model.calls += 1
            raise AssertionError("must not synthesise")

    backend = chatterbox.ChatterboxBackend(tmp_path / "out")
    backend._model = _Model()
    with pytest.raises(voice_consent.ConsentRequired):
        backend.generate(text="hi", reference_clip_path=str(clip))
    assert _Model.calls == 0


def test_auto_with_a_clip_never_falls_back_to_another_voice(plugin_import, tmp_path):
    voice_gen = plugin_import("backends.voice_gen")
    voice_consent = plugin_import("backends.voice_consent")
    from backends.base import AudioBackend, GenerationResult

    class _Inner(AudioBackend):
        def __init__(self, name, error=None):
            self.name, self.vram_mb_estimate, self.error, self.calls = name, 1, error, 0

        @property
        def is_loaded(self):
            return True

        def load(self):
            pass

        def unload(self):
            pass

        def generate(self, **params):
            self.calls += 1
            if self.error:
                raise self.error
            return GenerationResult(path=tmp_path / "x.wav", duration_s=1.0, sample_rate=24000,
                                    meta={"backend": self.name})

    vg = voice_gen.VoiceGenBackend(output_root=tmp_path)
    vg._chatterbox = _Inner("chatterbox", voice_consent.ConsentRequired("no consent"))
    vg._kokoro = _Inner("kokoro")
    with pytest.raises(voice_consent.ConsentRequired):
        vg.generate(text="hi", backend="auto", reference_clip_path="/x/me.wav")
    assert vg._kokoro.calls == 0

    # With nothing named, auto still falls back as before.
    vg._chatterbox.error = RuntimeError("out of memory")
    assert vg.generate(text="hi", backend="auto").meta["backend"] == "kokoro"
