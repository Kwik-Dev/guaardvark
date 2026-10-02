"""Kokoro reads only local files and only catalog voices.

A voice pack that is not cached, or spaCy's English model missing from the
venv, is refused with the Manage-models hint instead of being downloaded. A
voice id outside backends/kokoro_voices.json never reaches KPipeline, which
would torch.load a value ending in '.pt' and split one containing commas. The
kokoro package and the HF cache are not touched.
"""
from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from backends import kokoro_voices  # noqa: E402
from backends import voice_gen_kokoro as vgk  # noqa: E402
from backends.hub_weights import WeightsNotInstalled  # noqa: E402


@pytest.fixture
def kokoro(tmp_path):
    return vgk.KokoroBackend(output_root=tmp_path)


def test_catalog_voice_resolves_to_its_cached_path(kokoro, monkeypatch):
    seen = []

    def fake_cached(repo, filename):
        seen.append((repo, filename))
        return f"/cache/{filename}"

    monkeypatch.setattr(vgk, "cached_hub_file", fake_cached)
    assert kokoro._voice_path("bf_emma") == "/cache/voices/bf_emma.pt"
    assert seen == [("hexgrad/Kokoro-82M", "voices/bf_emma.pt")]


def test_uncached_voice_says_install(kokoro, monkeypatch):
    monkeypatch.setattr(vgk, "cached_hub_file", lambda repo, filename: None)
    with pytest.raises(WeightsNotInstalled, match="Manage models"):
        kokoro._voice_path("bf_emma")


@pytest.mark.parametrize("voice", ["/etc/passwd.pt", "voices/af_heart.pt", "af_heart,af_bella",
                                   "xyz", "", "AF_HEART"])
def test_non_catalog_voice_is_refused_before_any_lookup(kokoro, monkeypatch, voice):
    monkeypatch.setattr(vgk, "cached_hub_file",
                        lambda *a: pytest.fail("cache looked up for a rejected voice"))
    with pytest.raises(kokoro_voices.UnknownVoice):
        kokoro._voice_path(voice)


def test_english_voices_need_the_spacy_model(monkeypatch):
    real = metadata.distribution

    def hidden(name):
        if name == kokoro_voices.english_g2p()["package"]:
            raise metadata.PackageNotFoundError(name)
        return real(name)

    monkeypatch.setattr(metadata, "distribution", hidden)
    for lang in ("a", "b"):
        with pytest.raises(WeightsNotInstalled, match="en_core_web_sm"):
            vgk.KokoroBackend._require_english_g2p(lang)
    vgk.KokoroBackend._require_english_g2p("e")  # Spanish uses espeak, not spaCy


def test_catalog_ids_match_the_voice_id_shape():
    ids = kokoro_voices.voice_ids()
    assert ids and all(kokoro_voices.VOICE_ID_PATTERN.match(v) for v in ids)
    assert kokoro_voices.default_voice() in ids
    for v in ids:
        assert vgk.KokoroBackend._lang_code_for(v) == v[0]


def test_voices_route_marks_installed_packs_and_sends_no_cors(monkeypatch):
    from fastapi.testclient import TestClient
    from service.app import app

    monkeypatch.setattr("backends.hub_weights.cached_hub_file",
                        lambda repo, f: "/cache/x" if f == "voices/af_heart.pt" else None)
    r = TestClient(app, base_url="http://127.0.0.1:8206").get("/voices", headers={"Origin": "http://elsewhere.example"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers
    voices = {v["id"]: v["installed"] for g in r.json()["kokoro"]["groups"] for v in g["voices"]}
    assert list(voices) == kokoro_voices.voice_ids()
    assert voices["af_heart"] is True and voices["bf_emma"] is False
