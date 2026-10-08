#!/usr/bin/env python3
"""The speech recognition model chosen in Settings → Voice.

Transcription that names no model uses the saved choice, and the default
stays tiny.en. A chosen model whose weights are gone falls back to tiny.en
when that is installed, warning once. Choosing never downloads, and only an
installed model can be chosen. Large v3 Turbo is offered behind Install.
"""

import logging
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask  # noqa: E402

import backend.api.voice_api as voice_api  # noqa: E402
from backend.utils import faster_whisper_utils as fw  # noqa: E402

TURBO = "large-v3-turbo"


def _client():
    app = Flask(__name__)
    app.register_blueprint(voice_api.voice_bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def saved(monkeypatch):
    """The settings table as a dict that get_setting and save_setting use."""
    store = {}
    monkeypatch.setattr(
        "backend.utils.settings_utils.get_setting",
        lambda key, default=None, cast=str: store.get(key, default),
    )
    monkeypatch.setattr(
        "backend.utils.settings_utils.save_setting",
        lambda key, value: store.__setitem__(key, value),
    )
    voice_api._speech_model_fallback_warned.clear()
    yield store
    voice_api._speech_model_fallback_warned.clear()


@pytest.fixture
def installed(monkeypatch):
    """The model ids complete on a machine that has faster-whisper."""
    ids = set()
    monkeypatch.setattr(fw, "FASTER_WHISPER_AVAILABLE", True)
    monkeypatch.setattr(
        voice_api, "_whisper_missing_parts",
        lambda backend_path, model_id, config: [] if model_id in ids else ["faster-whisper"],
    )
    monkeypatch.setattr(fw, "is_model_installed", lambda m: m in ids)
    return ids


@pytest.fixture
def no_downloads(monkeypatch):
    """Every way weights could be fetched, recording any call."""
    calls = []

    def refuse(name):
        def _refuse(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} called")
        return _refuse

    monkeypatch.setattr(voice_api, "_do_whisper_download", refuse("_do_whisper_download"))
    monkeypatch.setattr(voice_api, "_download_ggml_model_direct", refuse("_download_ggml_model_direct"))
    monkeypatch.setattr(fw, "install_model", refuse("install_model"))
    return calls


# --- the saved choice ----------------------------------------------------------


def test_default_is_tiny_en_and_needs_no_install_check(saved):
    check = MagicMock()
    assert voice_api.get_speech_model() == "tiny.en"
    assert voice_api.resolve_speech_model(check) == "tiny.en"
    check.assert_not_called()


def test_unknown_saved_value_reads_as_the_default(saved):
    saved[voice_api.SPEECH_MODEL_SETTING] = "whisper-xxl"
    assert voice_api.get_speech_model() == "tiny.en"


def test_installed_choice_is_used(saved):
    saved[voice_api.SPEECH_MODEL_SETTING] = "small"
    assert voice_api.resolve_speech_model(lambda m: True) == "small"


def test_deleted_choice_falls_back_to_tiny_en_with_one_warning(saved, caplog):
    saved[voice_api.SPEECH_MODEL_SETTING] = "small"
    with caplog.at_level(logging.WARNING, logger=voice_api.logger.name):
        first = voice_api.resolve_speech_model(lambda m: m == "tiny.en")
        second = voice_api.resolve_speech_model(lambda m: m == "tiny.en")
    assert (first, second) == ("tiny.en", "tiny.en")
    assert len([r for r in caplog.records if "'small'" in r.getMessage()]) == 1


def test_deleted_choice_without_tiny_en_is_reported_by_name(saved):
    saved[voice_api.SPEECH_MODEL_SETTING] = "small"
    assert voice_api.resolve_speech_model(lambda m: False) == "small"


def test_duration_pick_never_replaces_a_choice():
    # Unpreferred, 400 s would pick "small" and 20 s "tiny".
    assert voice_api.select_optimal_whisper_model(400, "base") is voice_api.WHISPER_MODELS["base"]
    assert voice_api.select_optimal_whisper_model(20, TURBO) is voice_api.WHISPER_MODELS[TURBO]


# --- choosing ----------------------------------------------------------------------


def test_choices_are_only_installed_models(saved, installed):
    installed.update({"tiny.en", TURBO})
    body = _client().get("/api/voice/speech-model").get_json()["data"]
    assert [m["id"] for m in body["installed"]] == ["tiny.en", TURBO]
    assert body["model"] == body["in_use"] == body["default_model"] == "tiny.en"


def test_in_use_shows_the_fallback(saved, installed):
    installed.add("tiny.en")
    saved[voice_api.SPEECH_MODEL_SETTING] = "small"
    body = _client().get("/api/voice/speech-model").get_json()["data"]
    assert (body["model"], body["in_use"]) == ("small", "tiny.en")
    assert body["model_name"] == "Small (Accurate)"
    assert [m["id"] for m in body["installed"]] == ["tiny.en"]


def test_no_choices_without_a_speech_engine(monkeypatch):
    monkeypatch.setattr(fw, "FASTER_WHISPER_AVAILABLE", False)
    with patch("os.path.exists", return_value=False):
        assert voice_api._installed_whisper_models("/backend") == []


def test_choosing_an_installed_model_saves_it_without_a_download(saved, installed, no_downloads):
    installed.update({"tiny.en", "small"})
    response = _client().post("/api/voice/speech-model", json={"model": "small"})
    assert response.status_code == 200
    assert saved[voice_api.SPEECH_MODEL_SETTING] == "small"
    body = response.get_json()["data"]
    assert body["model"] == body["in_use"] == "small"
    assert no_downloads == []


def test_choosing_a_model_that_is_not_installed_is_refused(saved, installed, no_downloads):
    installed.add("tiny.en")
    response = _client().post("/api/voice/speech-model", json={"model": TURBO})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "SPEECH_MODEL_NOT_INSTALLED"
    assert voice_api.SPEECH_MODEL_SETTING not in saved
    assert no_downloads == []


def test_choosing_an_unknown_model_is_refused(saved, installed):
    response = _client().post("/api/voice/speech-model", json={"model": "whisper-xxl"})
    assert response.status_code == 400
    assert voice_api.SPEECH_MODEL_SETTING not in saved


def test_a_choice_that_did_not_save_is_reported(installed, monkeypatch):
    installed.update({"tiny.en", "small"})
    monkeypatch.setattr("backend.utils.settings_utils.get_setting", lambda key, default=None, cast=str: default)
    monkeypatch.setattr("backend.utils.settings_utils.save_setting", lambda key, value: None)
    response = _client().post("/api/voice/speech-model", json={"model": "small"})
    assert response.status_code == 500


# --- Large v3 Turbo in the model lists ------------------------------------------------


def test_turbo_is_offered_behind_install(saved, installed):
    installed.add("tiny.en")
    models = _client().get("/api/voice/models/all").get_json()["data"]["models"]
    turbo = next(m for m in models if m["id"] == TURBO)
    assert turbo["name"] == "Large v3 Turbo (Most accurate)"
    assert turbo["model_type"] == "whisper"
    assert turbo["is_downloaded"] is False
    assert turbo["size_mb"] == voice_api.WHISPER_MODEL_SIZES_MB[TURBO]


def test_turbo_listing_has_no_made_up_speed():
    with patch("os.path.exists", return_value=False):
        body = _client().get("/api/voice/models").get_json()
    assert body["models"][TURBO]["avg_processing_ratio"] is None


@pytest.mark.skipif(not fw.FASTER_WHISPER_AVAILABLE, reason="faster-whisper is not installed")
def test_faster_whisper_resolves_turbo():
    from faster_whisper.utils import _MODELS

    assert _MODELS[TURBO] == "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    assert fw.weights_cache_dir(TURBO).endswith("models--mobiuslabsgmbh--faster-whisper-large-v3-turbo")
