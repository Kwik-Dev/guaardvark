"""Audio generations are recorded in the main DB, not only in the sidecar (issue #8).

The Audio Foundry sidecar keeps its job history in memory, so a song/SFX/voice render
left no durable record and could not be rebuilt from the CLI. The backend now mirrors
every accepted ``/generate/...`` request into ``AudioGeneration`` (and annotates the
response with ``generation_id``), and moves async rows to their terminal state when the
job is polled.

Runs on in-memory SQLite with the sidecar proxied away: no GPU, no network, no service.
"""

from __future__ import annotations

import pytest
from flask import Flask, jsonify

from backend.api import audio_foundry_api as api
from backend.models import AudioGeneration, db


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      STORAGE_DIR=str(tmp_path))
    db.init_app(app)
    app.register_blueprint(api.audio_foundry_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _rows():
    return AudioGeneration.query.order_by(AudioGeneration.id).all()


def test_sync_voice_records_the_request_and_annotates_the_response(client, monkeypatch):
    monkeypatch.setattr(api, "_proxy_generate", lambda path, payload: (
        jsonify({"path": "/out/voice_1.wav", "duration_s": 2.43, "document_id": 77,
                 "meta": {"backend": "kokoro"}}), 200))

    res = client.post("/api/audio-foundry/generate/voice", json={"text": "Hello there."})
    assert res.status_code == 200
    body = res.get_json()
    assert body["generation_id"] == 1

    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "voice"
    assert row.status == "completed"
    assert row.document_id == 77
    assert row.output_path == "/out/voice_1.wav"
    assert row.duration_s == 2.43
    assert row.inputs == {"text": "Hello there."}


def test_sync_music_records_model_and_seed(client, monkeypatch):
    monkeypatch.setattr(api, "_proxy_generate", lambda path, payload: (
        jsonify({"path": "/out/song.wav", "duration_s": 30.0, "document_id": 5}), 200))

    res = client.post("/api/audio-foundry/generate/music",
                      json={"style_prompt": "lo-fi piano", "duration_s": 30.0,
                            "instrumental_only": True, "async": True, "model": "acestep",
                            "seed": 42})
    assert res.status_code == 200

    row = _rows()[0]
    assert row.kind == "music"
    assert row.model == "acestep" and row.seed == 42
    assert row.inputs["style_prompt"] == "lo-fi piano"
    assert "lyrics" not in row.inputs


def test_async_music_is_queued_then_finishes_when_polled(client, monkeypatch):
    job_id = "a" * 32
    monkeypatch.setattr(api, "_proxy_generate", lambda path, payload: (
        jsonify({"mode": "async", "job_id": job_id, "status": "queued",
                 "estimate_s": 45.0}), 202))

    res = client.post("/api/audio-foundry/generate/music", json={"style_prompt": "ambient"})
    assert res.status_code == 202
    row = _rows()[0]
    assert row.status == "queued" and row.job_id == job_id

    # The worker finishes; polling the job moves the record to completed with its output.
    monkeypatch.setattr(api, "_proxy_get", lambda path, timeout=api.QUICK_TIMEOUT: (
        jsonify({"id": job_id, "status": "done", "progress": {"current": 1, "total": 1},
                 "result": {"path": "/out/song.wav", "duration_s": 30.0, "document_id": 9},
                 "error": None}), 200))

    res = client.get(f"/api/audio-foundry/jobs/{job_id}")
    assert res.status_code == 200
    db.session.expire_all()
    row = _rows()[0]
    assert row.status == "completed"
    assert row.output_path == "/out/song.wav" and row.document_id == 9


def test_a_refused_request_is_not_recorded(client, monkeypatch):
    monkeypatch.setattr(api, "_proxy_generate", lambda path, payload: (
        jsonify({"error": "plugin offline", "plugin_running": False}), 503))

    res = client.post("/api/audio-foundry/generate/fx", json={"prompt": "rain"})
    assert res.status_code == 503
    assert _rows() == []


def test_generations_list_narrows_by_kind_and_get_returns_one(client):
    with client.application.app_context():
        db.session.add(AudioGeneration(kind="music", status="completed",
                                       inputs={"style_prompt": "a"}))
        db.session.add(AudioGeneration(kind="sfx", status="queued", inputs={"prompt": "b"}))
        db.session.commit()

    listed = client.get("/api/audio-foundry/generations").get_json()["generations"]
    assert [g["kind"] for g in listed] == ["sfx", "music"]  # newest first

    only_music = client.get("/api/audio-foundry/generations?kind=music").get_json()["generations"]
    assert len(only_music) == 1 and only_music[0]["kind"] == "music"

    one = client.get(f"/api/audio-foundry/generations/{only_music[0]['id']}").get_json()
    assert one["generation"]["inputs"] == {"style_prompt": "a"}

    missing = client.get("/api/audio-foundry/generations/99999")
    assert missing.status_code == 404
