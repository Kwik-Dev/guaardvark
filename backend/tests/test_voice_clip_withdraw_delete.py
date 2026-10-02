"""Taking a voice clip back in Audio Studio.

Withdrawing consent removes the clip's consent record and keeps the clip, which
is then refused for cloning until consent is confirmed again. Deleting removes
the clip and its record, and only that clip. Deleting answers this machine or
the API key, like the Cast Library's deletes; withdrawing stays as open as
giving consent. The voice list answers while Audio Foundry is stopped.

Flask's test client against temporary upload folders, behind the real auth
hook. Audio Foundry is never called: its generate proxy and GET /voices are
stand-ins. No GPU, network or database.
"""

from __future__ import annotations

import io
import os
from types import SimpleNamespace
from urllib.parse import quote

import pytest
import requests
from flask import Flask

WAV = b"RIFF0000WAVEfmt voice"
REMOTE = "192.0.2.10"  # TEST-NET-1: never one of this machine's addresses
LOCAL = "127.0.0.1"
CONFIRMED = {"confirmed": True}
BASE = "/api/audio-foundry/voice-clips"


@pytest.fixture
def studio(monkeypatch, tmp_path):
    from backend.api import audio_foundry_api
    from backend.utils import auth_guard

    # Pin the machine's own addresses so no interface probe runs.
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    app = Flask(__name__)
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    app.before_request(auth_guard.check_endpoint_auth)
    app.register_blueprint(audio_foundry_api.audio_foundry_bp)
    forwarded = []

    def fake_generate(path, data):
        forwarded.append((path, data))
        return {"path": "/x/out.wav"}, 200

    monkeypatch.setattr(audio_foundry_api, "_proxy_generate", fake_generate)
    return SimpleNamespace(app=app, client=app.test_client(), forwarded=forwarded,
                           refs=tmp_path / "uploads" / "voice_references", guard=auth_guard)


@pytest.fixture
def consent():
    from backend.services.audio_foundry_models import voice_consent
    return voice_consent()


def _upload(client, name="me.wav", data=WAV, **form):
    body = {"file": (io.BytesIO(data), name), "name": name, **form}
    res = client.post(f"{BASE}/upload", data=body, content_type="multipart/form-data")
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def _clone(client, clip_path):
    return client.post("/api/audio-foundry/generate/voice",
                       json={"text": "hi", "backend": "chatterbox", "reference_clip_path": str(clip_path)})


def _listed(client):
    return {c["filename"]: c for c in client.get(BASE).get_json()["clips"]}


# ---- withdraw consent ----------------------------------------------------------------
def test_withdrawing_keeps_the_clip_and_stops_cloning_until_consent_is_given_again(studio):
    up = _upload(studio.client, consent_confirmed="true")
    assert up["consented"] is True

    res = studio.client.delete(f"{BASE}/me.wav/consent", json=CONFIRMED)
    assert res.status_code == 200
    assert res.get_json() == {"id": "me", "filename": "me.wav", "consented": False, "withdrawn": True}
    assert (studio.refs / "me.wav").read_bytes() == WAV
    assert not (studio.refs / "me.wav.consent").exists()
    assert _listed(studio.client)["me.wav"]["consented"] is False

    refused = _clone(studio.client, up["path"])
    assert refused.status_code == 403 and refused.get_json()["needs_consent"] is True
    assert studio.forwarded == []

    assert studio.client.post(f"{BASE}/me.wav/consent", json=CONFIRMED).status_code == 200
    assert _clone(studio.client, up["path"]).status_code == 200


def test_withdrawing_takes_only_a_confirmed_json_request(studio):
    _upload(studio.client, consent_confirmed="true")
    record = studio.refs / "me.wav.consent"
    for kwargs in ({}, {"data": {"confirmed": "true"}}, {"json": {}}, {"json": {"confirmed": "true"}},
                   {"json": ["confirmed"]}):
        res = studio.client.delete(f"{BASE}/me/consent", **kwargs)
        assert res.status_code == 400, kwargs
        assert record.exists()
    assert studio.client.delete(f"{BASE}/nope/consent", json=CONFIRMED).status_code == 404
    assert studio.client.delete(f"{BASE}/bad*name/consent", json=CONFIRMED).status_code == 400


def test_withdrawing_a_clip_without_consent_says_nothing_was_withdrawn(studio):
    _upload(studio.client)
    (studio.refs / "me.wav.consent").touch()  # what uploads used to leave behind: not a record
    res = studio.client.delete(f"{BASE}/me/consent", json=CONFIRMED)
    assert res.status_code == 200 and res.get_json()["withdrawn"] is False
    assert not (studio.refs / "me.wav.consent").exists()
    assert (studio.refs / "me.wav").exists()


def test_a_symlinked_record_is_removed_without_touching_its_target(consent, tmp_path):
    refs = tmp_path / "voice_references"
    refs.mkdir()
    clip = refs / "me.wav"
    clip.write_bytes(WAV)
    elsewhere = tmp_path / "keep.json"
    elsewhere.write_text("{}")
    os.symlink(elsewhere, refs / "me.wav.consent")
    assert consent.remove_record(clip) is False
    assert not os.path.lexists(refs / "me.wav.consent")
    assert elsewhere.read_text() == "{}"


# ---- delete clip ---------------------------------------------------------------------
def test_deleting_removes_the_clip_and_its_record_and_nothing_else(studio):
    me = _upload(studio.client, consent_confirmed="true")
    _upload(studio.client, "me.v2.wav", consent_confirmed="true")
    _upload(studio.client, "other.wav")

    res = studio.client.delete(f"{BASE}/me", json=CONFIRMED)
    assert res.status_code == 200 and res.get_json() == {"deleted": "me.wav", "id": "me"}
    assert not (studio.refs / "me.wav").exists()
    assert not (studio.refs / "me.wav.consent").exists()
    # A longer name that starts with the id is another clip.
    assert (studio.refs / "me.v2.wav").exists() and (studio.refs / "me.v2.wav.consent").exists()
    assert sorted(_listed(studio.client)) == ["me.v2.wav", "other.wav"]

    refused = _clone(studio.client, me["path"])
    assert refused.status_code == 403 and studio.forwarded == []
    assert studio.client.delete(f"{BASE}/me", json=CONFIRMED).status_code == 404


def test_deleting_takes_only_a_confirmed_json_request(studio):
    _upload(studio.client, consent_confirmed="true")
    for kwargs in ({}, {"data": {"confirmed": "true"}}, {"json": {"confirmed": False}}):
        assert studio.client.delete(f"{BASE}/me", **kwargs).status_code == 400, kwargs
    assert (studio.refs / "me.wav").exists() and (studio.refs / "me.wav.consent").exists()


def test_a_file_name_tells_apart_clips_that_share_an_id(studio):
    _upload(studio.client, "me.wav", consent_confirmed="true")
    _upload(studio.client, "me.mp3", data=b"ID3 voice", consent_confirmed="true")
    assert studio.client.delete(f"{BASE}/me.wav/consent", json=CONFIRMED).status_code == 200
    listed = _listed(studio.client)
    assert listed["me.wav"]["consented"] is False and listed["me.mp3"]["consented"] is True

    assert studio.client.delete(f"{BASE}/me.wav", json=CONFIRMED).get_json()["deleted"] == "me.wav"
    assert sorted(_listed(studio.client)) == ["me.mp3"]


def test_a_clip_renamed_on_import_can_be_played_confirmed_withdrawn_and_deleted(studio):
    _upload(studio.client)
    second = _upload(studio.client)  # same name: the import adds " (2)"
    assert second["filename"] == "me (2).wav" and second["id"] == "me (2)"
    by_id = f"{BASE}/{quote(second['id'])}"
    by_name = f"{BASE}/{quote(second['filename'])}"

    assert studio.client.get(f"{by_id}/download").status_code == 200
    assert studio.client.post(f"{by_name}/consent", json=CONFIRMED).status_code == 200
    assert studio.client.delete(f"{by_id}/consent", json=CONFIRMED).get_json()["withdrawn"] is True
    assert studio.client.delete(by_name, json=CONFIRMED).get_json()["deleted"] == "me (2).wav"
    assert sorted(_listed(studio.client)) == ["me.wav"]


def test_an_import_does_not_inherit_a_record_left_by_a_removed_clip(studio, consent):
    studio.refs.mkdir(parents=True)
    old = studio.refs / "me.wav"
    old.write_bytes(WAV)
    consent.write_record(old, source="audio_studio")
    old.unlink()  # removed outside the Studio; its record stayed

    up = _upload(studio.client)  # the same recording, imported without confirmation
    assert up["filename"] == "me.wav" and up["consented"] is False
    assert not (studio.refs / "me.wav.consent").exists()
    assert _clone(studio.client, up["path"]).status_code == 403


# ---- who may do it -------------------------------------------------------------------
@pytest.mark.parametrize("method,path,protected", [
    ("DELETE", f"{BASE}/me", True),
    ("DELETE", f"{BASE}/me.wav", True),
    ("DELETE", f"{BASE}/me/", True),
    ("DELETE", f"{BASE}/me/consent", False),
    ("POST", f"{BASE}/me/consent", False),
    ("POST", f"{BASE}/upload", False),
    ("GET", f"{BASE}/me/download", False),
    ("GET", BASE, False),
])
def test_only_deleting_a_clip_is_protected(studio, method, path, protected):
    with studio.app.test_request_context(path, method=method):
        assert studio.guard._is_protected() is protected


def test_another_device_may_withdraw_consent_but_not_delete_without_the_key(studio):
    _upload(studio.client, consent_confirmed="true")
    remote = {"REMOTE_ADDR": REMOTE}

    refused = studio.client.delete(f"{BASE}/me", json=CONFIRMED, environ_base=remote)
    assert refused.status_code == 403 and refused.get_json()["code"] == "local_only"
    assert (studio.refs / "me.wav").exists()
    # Through the local proxy a LAN device is still remote.
    assert studio.client.delete(f"{BASE}/me", json=CONFIRMED,
                                headers={"X-Forwarded-For": REMOTE}).status_code == 403

    withdrawn = studio.client.delete(f"{BASE}/me/consent", json=CONFIRMED, environ_base=remote)
    assert withdrawn.status_code == 200 and withdrawn.get_json()["withdrawn"] is True

    assert studio.client.delete(f"{BASE}/me", json=CONFIRMED, environ_base={"REMOTE_ADDR": LOCAL}).status_code == 200


def test_with_an_api_key_deleting_needs_it_on_this_machine_too(studio, monkeypatch):
    _upload(studio.client)
    key = "k" * 43
    monkeypatch.setenv("GUAARDVARK_API_KEY", key)
    refused = studio.client.delete(f"{BASE}/me", json=CONFIRMED)
    assert refused.status_code == 401 and refused.get_json()["code"] == "api_key_required"
    assert studio.client.delete(f"{BASE}/me", json=CONFIRMED, headers={"X-API-Key": key}).status_code == 200


# ---- the voice list ------------------------------------------------------------------
def test_the_voice_list_is_read_from_the_checkout_while_the_plugin_is_stopped(studio, monkeypatch):
    from backend.api import audio_foundry_api
    from backend.services import audio_foundry_models

    def down(*_a, **_k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(audio_foundry_api.requests, "get", down)
    monkeypatch.setattr(audio_foundry_models, "is_hub_cached",
                        lambda repo, name: name.endswith("/af_heart.pt"))

    res = studio.client.get("/api/audio-foundry/voices")
    assert res.status_code == 200
    body = res.get_json()
    assert body["plugin_running"] is False and body["kokoro"]["default"] == "af_heart"
    voices = [v for g in body["kokoro"]["groups"] for v in g["voices"]]
    assert [v["id"] for v in voices] == audio_foundry_models.kokoro_voice_ids()
    assert {v["id"] for v in voices if v["installed"]} == {"af_heart"}
    assert all(v["label"] for v in voices)


def test_the_voice_list_comes_from_the_plugin_while_it_runs(studio, monkeypatch):
    from backend.api import audio_foundry_api

    answer = {"kokoro": {"default": "af_heart", "groups": []}, "chatterbox": {"type": "reference_clip"}}
    monkeypatch.setattr(audio_foundry_api.requests, "get",
                        lambda url, timeout: SimpleNamespace(status_code=200, json=lambda: answer))
    res = studio.client.get("/api/audio-foundry/voices")
    assert res.status_code == 200 and res.get_json() == answer
