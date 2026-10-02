"""Tests for POST /api/cast-library/subjects/<id>/import-lora.

Uses tiny synthetic .safetensors fixtures built from the real key layouts
(diffusers/PEFT for Z-Image Turbo, kohya for FLUX.1 Dev) instead of the
actual multi-hundred-MB checkpoints, per the maintainer's suggestion on
issue #245. Each fixture is a few KB: real key names, 1x1 dummy tensors.

Note: the endpoint reads STORAGE_DIR from backend.config (a module-level
constant set once at import time), not from current_app.config — unlike the
older cast-ref upload endpoint. So the app fixture's app.config override
does NOT redirect it; tests must monkeypatch the name as it's bound inside
backend.api.cast_library_api itself, or imported LoRAs land in the repo's
real data/training/loras/ during CI.
"""
import io
import json
import struct

import pytest

try:
    from flask import Flask
    from backend.models import db, Subject
    import backend.api.cast_library_api as cast_library_api
    from backend.api.cast_library_api import bp as cast_library_bp
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


def _build_safetensors(keys: list[str]) -> bytes:
    """Minimal valid .safetensors: JSON header + 1x1 f32 tensor per key."""
    header = {}
    offset = 0
    for key in keys:
        header[key] = {"dtype": "F32", "shape": [1, 1], "data_offsets": [offset, offset + 4]}
        offset += 4
    header_bytes = json.dumps(header).encode("utf-8")
    return struct.pack("<Q", len(header_bytes)) + header_bytes + (b"\x00" * offset)


def _zimage_keys() -> list[str]:
    modules = [
        "attention.to_q", "attention.to_k", "attention.to_v", "attention.to_out.0",
        "feed_forward.w1", "feed_forward.w2", "feed_forward.w3", "adaLN_modulation.0",
    ]
    keys = []
    for layer in range(3):
        for module in modules:
            base = f"diffusion_model.layers.{layer}.{module}"
            keys.append(f"{base}.lora_A.weight")
            keys.append(f"{base}.lora_B.weight")
    return keys


def _flux_keys() -> list[str]:
    keys = []
    for block in range(2):
        for suffix in ("img_attn_qkv", "img_mlp_0", "txt_attn_qkv"):
            base = f"lora_unet_double_blocks_{block}_{suffix}"
            keys.append(f"{base}.lora_down.weight")
            keys.append(f"{base}.lora_up.weight")
            keys.append(f"{base}.alpha")
    for block in range(2):
        base = f"lora_unet_single_blocks_{block}_linear1"
        keys.append(f"{base}.lora_down.weight")
        keys.append(f"{base}.lora_up.weight")
    return keys


def _sdxl_keys() -> list[str]:
    return [
        "lora_unet_down_blocks_0_attentions_0_proj_in.lora_down.weight",
        "lora_unet_down_blocks_0_attentions_0_proj_in.lora_up.weight",
        "lora_unet_up_blocks_0_attentions_0_proj_out.lora_down.weight",
        "lora_te_text_model_encoder_layers_0_mlp_fc1.lora_down.weight",
    ]


def _flux_keys_with_text_encoder() -> list[str]:
    """FLUX keys plus a text-encoder component (lora_te1_*), which _SDXL_RE
    also matches since it starts with "lora_te". A real CLIP-L text encoder
    has enough layers that this pushes the flux match ratio below the 90%
    threshold. Pins today's behaviour: the file is turned away rather than
    accepted as FLUX with the text-encoder component ignored (see the
    docstring note on _detect_lora_family)."""
    te_keys = []
    for layer in range(6):
        base = f"lora_te1_text_model_encoder_layers_{layer}_mlp_fc1"
        te_keys.append(f"{base}.lora_down.weight")
        te_keys.append(f"{base}.lora_up.weight")
    return _flux_keys() + te_keys


@pytest.fixture
def app(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    # See module docstring: STORAGE_DIR is read as a module-level name inside
    # cast_library_api, not via current_app.config — patch it there.
    monkeypatch.setattr(cast_library_api, "STORAGE_DIR", str(tmp_path))
    db.init_app(app)
    app.register_blueprint(cast_library_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _create_subject(client, name="Test Subject"):
    resp = client.post("/api/cast-library/subjects", json={"kind": "character", "name": name})
    assert resp.status_code == 201
    return resp.get_json()["id"]


def test_import_zimage_lora_succeeds(client):
    subject_id = _create_subject(client)
    data = _build_safetensors(_zimage_keys())
    resp = client.post(
        f"/api/cast-library/subjects/{subject_id}/import-lora",
        data={
            "lora_file": (io.BytesIO(data), "zimage.safetensors"),
            "base_model_id": "zimage-turbo",
            "trigger_word": "caroline_1",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_json()
    subject = resp.get_json()["subject"]
    assert subject["training_status"] == "trained"
    assert subject["trigger_word"] == "caroline_1"
    assert subject["lora_version"] == 1
    assert subject["lora_path"].endswith("subject_%d_imported_v1.safetensors" % subject_id)


def test_import_flux_lora_succeeds(client):
    subject_id = _create_subject(client)
    data = _build_safetensors(_flux_keys())
    resp = client.post(
        f"/api/cast-library/subjects/{subject_id}/import-lora",
        data={
            "lora_file": (io.BytesIO(data), "flux.safetensors"),
            "base_model_id": "flux-dev",
            "trigger_word": "caroline_1",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.get_json()
    subject = resp.get_json()["subject"]
    assert subject["training_status"] == "trained"
    assert subject["training_settings_json"]["base_model_id"] == "flux-dev"


def test_import_rejects_mismatched_family(client):
    subject_id = _create_subject(client)
    data = _build_safetensors(_zimage_keys())
    resp = client.post(
        f"/api/cast-library/subjects/{subject_id}/import-lora",
        data={
            "lora_file": (io.BytesIO(data), "zimage.safetensors"),
            "base_model_id": "flux-dev",
            "trigger_word": "caroline_1",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "does not match" in resp.get_json()["error"]


def test_import_rejects_sdxl_layout(client):
    subject_id = _create_subject(client)
    data = _build_safetensors(_sdxl_keys())
    resp = client.post(
        f"/api/cast-library/subjects/{subject_id}/import-lora",
        data={
            "lora_file": (io.BytesIO(data), "sdxl.safetensors"),
            "base_model_id": "zimage-turbo",
            "trigger_word": "caroline_1",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    # A true SDXL LoRA (no FLUX-shaped U-Net keys underneath) gets the generic
    # message, not the FLUX-text-encoder hint below.
    assert "SDXL LoRAs are not supported" in resp.get_json()["error"]


def test_import_rejects_flux_with_text_encoder_keys(client):
    subject_id = _create_subject(client)
    data = _build_safetensors(_flux_keys_with_text_encoder())
    resp = client.post(
        f"/api/cast-library/subjects/{subject_id}/import-lora",
        data={
            "lora_file": (io.BytesIO(data), "flux_with_te.safetensors"),
            "base_model_id": "flux-dev",
            "trigger_word": "caroline_1",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    # Still rejected (pinned behaviour), but with a message that points at the
    # actual cause instead of the generic SDXL rejection — see the maintainer's
    # review on PR #253.
    assert "text encoder" in resp.get_json()["error"]
    assert "SDXL" not in resp.get_json()["error"]


def test_import_returns_409_while_training(client, app):
    """Mirrors dispatch_train's own guard: importing over a Subject that's
    mid-training-run must not silently mark it trained and get clobbered when
    the run finishes — see the maintainer's review on PR #253."""
    subject_id = _create_subject(client)
    with app.app_context():
        s = db.session.get(Subject, subject_id)
        s.training_status = "training"
        db.session.commit()

    data = _build_safetensors(_zimage_keys())
    resp = client.post(
        f"/api/cast-library/subjects/{subject_id}/import-lora",
        data={
            "lora_file": (io.BytesIO(data), "zimage.safetensors"),
            "base_model_id": "zimage-turbo",
            "trigger_word": "caroline_1",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "already_training"


def test_import_requires_trigger_word(client):
    subject_id = _create_subject(client)
    data = _build_safetensors(_zimage_keys())
    resp = client.post(
        f"/api/cast-library/subjects/{subject_id}/import-lora",
        data={
            "lora_file": (io.BytesIO(data), "zimage.safetensors"),
            "base_model_id": "zimage-turbo",
            "trigger_word": "",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_import_rejects_non_safetensors_extension(client):
    subject_id = _create_subject(client)
    resp = client.post(
        f"/api/cast-library/subjects/{subject_id}/import-lora",
        data={
            "lora_file": (io.BytesIO(b"not a real file"), "lora.bin"),
            "base_model_id": "zimage-turbo",
            "trigger_word": "caroline_1",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_import_unknown_subject_returns_404(client):
    data = _build_safetensors(_zimage_keys())
    resp = client.post(
        "/api/cast-library/subjects/99999/import-lora",
        data={
            "lora_file": (io.BytesIO(data), "zimage.safetensors"),
            "base_model_id": "zimage-turbo",
            "trigger_word": "caroline_1",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 404


def test_reimport_bumps_version(client):
    subject_id = _create_subject(client)
    data = _build_safetensors(_zimage_keys())
    for expected_version in (1, 2):
        resp = client.post(
            f"/api/cast-library/subjects/{subject_id}/import-lora",
            data={
                "lora_file": (io.BytesIO(data), "zimage.safetensors"),
                "base_model_id": "zimage-turbo",
                "trigger_word": "caroline_1",
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["subject"]["lora_version"] == expected_version
