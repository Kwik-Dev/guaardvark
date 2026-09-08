"""GET /api/batch-video/models carries the capability contract and the tier
defaults resolved for this card, so the page and the tools read data."""

from __future__ import annotations

import pytest

try:
    from flask import Flask
    from backend.api import batch_video_generation_api as api
    from backend.api.batch_video_generation_api import batch_video_bp
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config.update({"TESTING": True})
    app.register_blueprint(batch_video_bp)
    # No disk, no GPU: every model reads as not downloaded on a 16 GB card.
    monkeypatch.setattr(api, "_check_model_downloaded", lambda _m: False)
    monkeypatch.setattr(api, "_missing_check_files", lambda _m: [])
    monkeypatch.setattr(api, "_detected_total_vram_mb", lambda: 16376)
    monkeypatch.setattr(api, "resolve_active_video_model", lambda role, explicit=None, surface=None: (None, "none"))
    return app.test_client()


def _rows(client):
    payload = client.get("/api/batch-video/models").get_json()
    assert payload["success"]
    return {m["id"]: m for m in payload["data"]["models"]}


def test_generation_rows_carry_capabilities_and_tier_defaults(client):
    rows = _rows(client)
    h3 = rows["minimax-h3-int8"]
    caps = h3["capabilities"]
    assert caps["modes"] == ["t2v", "i2v", "l2v", "flf2v"]
    assert caps["audio_out"] is True and caps["cfg"] is False
    assert caps["min_steps"] == 20 and "turbo-8" in caps["speed_profiles"]
    assert caps["aspect_ratios"][0] == "21:9"
    assert h3["tier_defaults"] == {
        "tier": "16", "width": 864, "height": 480, "speed_profile": "turbo-8", "frames": 124,
    }
    assert h3["license"]["attribution"] == "MiniMax H3"
    assert h3["license"]["form_url"].startswith("https://")


def test_older_families_get_defaults_and_companions_get_nothing(client):
    rows = _rows(client)
    wan = rows["wan22-5b"]
    assert wan["capabilities"]["modes"] == ["t2v", "i2v"]
    assert wan["capabilities"]["cfg"] is True and wan["license"] is None
    assert wan["tier_defaults"] == {}
    vae = rows["minimax-h3-vae"]
    assert vae["capabilities"] == {} and vae["tier_defaults"] == {}


def test_bigger_builds_have_no_defaults_for_a_smaller_card(client):
    rows = _rows(client)
    assert rows["minimax-h3-int8-full"]["tier_defaults"] == {}
    assert rows["minimax-h3-bf16"]["tier_defaults"] == {}


def test_loras_name_the_models_they_apply_to(client):
    rows = _rows(client)
    lora = rows["minimax-h3-fl2v-turbo-8step"]
    assert lora["type"] == "lora"
    assert "minimax-h3-int8" in lora["applies_to"]
    assert rows["minimax-h3-int8"]["applies_to"] == []


def test_from_hf_rejects_non_hf(client):
    payload = client.post("/api/batch-video/models/from-hf", json={"url": "https://civitai.com/x"}).get_json()
    assert payload["success"] is False
    assert "Hugging Face" in ((payload.get("error") or {}).get("message") or "")


def test_user_add_and_delete(client, tmp_path, monkeypatch):
    from backend.services import user_video_models as uvm
    monkeypatch.setattr(uvm, "_CATALOG_PATH_OVERRIDE", tmp_path / "user_video_models.json")
    body = {
        "role": "lora",
        "like": "minimax-h3-int8",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "files": [{"src": "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"}],
        "name": "API turbo",
        "install": False,
    }
    payload = client.post("/api/batch-video/models/user", json=body).get_json()
    assert payload["success"], payload
    mid = payload["data"]["id"]
    try:
        assert mid.startswith("user-")
        rows = _rows(client)
        assert rows[mid]["user"] is True
        assert rows[mid]["type"] == "lora"
        forbidden = client.delete("/api/batch-video/models/user/minimax-h3-int8").get_json()
        assert forbidden["success"] is False
        gone = client.delete(f"/api/batch-video/models/user/{mid}").get_json()
        assert gone["success"] is True
        assert mid not in _rows(client)
    finally:
        try:
            uvm.remove_user_model(mid, delete_files=False)
        except Exception:
            pass


def test_user_add_install_true_does_not_500(client, tmp_path, monkeypatch):
    from backend.services import user_video_models as uvm
    from backend.utils.response_utils import success_response
    monkeypatch.setattr(uvm, "_CATALOG_PATH_OVERRIDE", tmp_path / "user_video_models.json")
    monkeypatch.setattr(api, "start_video_model_download", lambda _mid: success_response({"message": "already installed"}))
    body = {
        "role": "lora",
        "like": "minimax-h3-int8",
        "hf_repo": "Comfy-Org/MiniMax-H3",
        "files": [{"src": "loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"}],
        "name": "Install path",
        "install": True,
    }
    payload = client.post("/api/batch-video/models/user", json=body).get_json()
    assert payload["success"], payload
    mid = payload["data"]["id"]
    try:
        assert payload["data"]["download"]["message"] == "already installed"
    finally:
        try:
            uvm.remove_user_model(mid, delete_files=False)
        except Exception:
            pass
