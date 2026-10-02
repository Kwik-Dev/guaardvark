"""Chatterbox speaks in its stock voice unless a request names a clip, and a
clone reads its clip once.

ChatterboxTTS keeps the conditionals of the last voice it was given
(``model.conds``), and ``generate(audio_prompt_path=...)`` replaces them with
the clip's. The backend puts the stock voice back after every generation, so a
request without a clip never speaks in the last cloned voice, including one
whose consent was withdrawn or whose clip was deleted. It prepares the clip's
conditionals once per generation, so a clip deleted while a clone runs does
not stop it, and it checks consent once it holds the model.

The plugin's own ChatterboxBackend with a stand-in for ChatterboxTTS that keeps
conditionals the way the library does. No GPU, weights or network.
"""

from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "audio_foundry"
TEXT = "First line here. Second line here. Third line here."  # three chunks at 20 chars


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


class FakeTTS:
    """chatterbox.tts.ChatterboxTTS as far as voices go: ``conds`` is the voice
    the next generate() speaks with, prepare_conditionals reads a clip and
    replaces it, and generate(audio_prompt_path=...) calls that first."""

    def __init__(self):
        self.conds = "stock"
        self.prepared = []
        self.spoken = []
        self.after_first_chunk = None

    def prepare_conditionals(self, wav_fpath, exaggeration=0.5):
        Path(wav_fpath).read_bytes()
        self.prepared.append(Path(wav_fpath).name)
        self.conds = f"clone:{Path(wav_fpath).name}"

    def generate(self, text, audio_prompt_path=None, exaggeration=0.5, cfg_weight=0.5, temperature=0.8):
        if audio_prompt_path:
            self.prepare_conditionals(audio_prompt_path, exaggeration=exaggeration)
        self.spoken.append(self.conds)
        if len(self.spoken) == 1 and self.after_first_chunk:
            self.after_first_chunk()
        return np.zeros(2400, dtype=np.float32)


def _withdraw(consent, clip):
    """Consent withdrawn: the clip's record is gone."""
    consent.record_path(clip).unlink()


@pytest.fixture
def studio(plugin_import, monkeypatch, tmp_path):
    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
    monkeypatch.delenv("GUAARDVARK_UPLOAD_DIR", raising=False)
    chatterbox = plugin_import("backends.voice_gen_chatterbox")
    consent = plugin_import("backends.voice_consent")

    backend = chatterbox.ChatterboxBackend(tmp_path / "out", chunk_chars=20)
    model = FakeTTS()
    # What load() leaves: the model, and its stock conditionals from conds.pt.
    backend._model = model
    backend._stock_conds = model.conds
    # Peak normalisation (pydub) is not what this tests.
    monkeypatch.setattr(backend, "post_process", lambda path, output_format="wav": path)

    refs = tmp_path / "data" / "uploads" / "voice_references"
    refs.mkdir(parents=True)
    clip = refs / "me.wav"
    clip.write_bytes(b"RIFF0000WAVEfmt voice")
    consent.write_record(clip, source="audio_studio")
    return SimpleNamespace(backend=backend, model=model, clip=clip, consent=consent)


def test_a_request_without_a_clip_speaks_the_stock_voice_after_a_clone(studio):
    studio.backend.generate(text=TEXT, reference_clip_path=str(studio.clip))
    assert studio.model.spoken == ["clone:me.wav"] * 3
    assert studio.model.conds == "stock"

    studio.model.spoken.clear()
    studio.backend.generate(text=TEXT)
    assert studio.model.spoken == ["stock"] * 3


def test_after_consent_is_withdrawn_the_voice_is_neither_cloned_nor_kept(studio):
    studio.backend.generate(text=TEXT, reference_clip_path=str(studio.clip))
    _withdraw(studio.consent, studio.clip)

    with pytest.raises(studio.consent.ConsentRequired):
        studio.backend.generate(text=TEXT, reference_clip_path=str(studio.clip))
    studio.model.spoken.clear()
    studio.backend.generate(text=TEXT)
    assert studio.model.spoken == ["stock"] * 3


def test_a_clone_reads_its_clip_once_and_finishes_if_the_clip_is_deleted(studio):
    def delete_clip():
        _withdraw(studio.consent, studio.clip)
        studio.clip.unlink()

    studio.model.after_first_chunk = delete_clip
    result = studio.backend.generate(text=TEXT, reference_clip_path=str(studio.clip))
    assert studio.model.prepared == ["me.wav"]
    assert studio.model.spoken == ["clone:me.wav"] * 3
    assert result.meta["chunks"] == 3 and Path(result.path).is_file()
    assert studio.model.conds == "stock"


def test_the_stock_voice_comes_back_when_a_clone_fails(studio):
    def fail():
        raise RuntimeError("out of memory")

    studio.model.after_first_chunk = fail
    with pytest.raises(RuntimeError, match="out of memory"):
        studio.backend.generate(text=TEXT, reference_clip_path=str(studio.clip))
    assert studio.model.conds == "stock"


def test_consent_is_checked_when_the_request_gets_the_model(studio):
    # A request waiting behind another generation sees consent withdrawn
    # while it waited.
    outcome = {}

    def request():
        try:
            outcome["result"] = studio.backend.generate(text=TEXT, reference_clip_path=str(studio.clip))
        except Exception as e:  # noqa: BLE001 - the test inspects it
            outcome["error"] = e

    with studio.backend._generate_lock:
        waiting = threading.Thread(target=request)
        waiting.start()
        waiting.join(timeout=0.3)
        assert waiting.is_alive()
        _withdraw(studio.consent, studio.clip)
    waiting.join(timeout=10)
    assert not waiting.is_alive()
    assert isinstance(outcome.get("error"), studio.consent.ConsentRequired)
    assert studio.model.spoken == []
