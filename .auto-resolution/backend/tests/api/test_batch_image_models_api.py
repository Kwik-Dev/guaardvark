"""Image models list + user-add API, mirroring the video suite."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

try:
    from flask import Flask
    from backend.api import batch_image_generation_api as api
    from backend.api.batch_image_generation_api import batch_image_bp
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


def _generator(tmp_path):
    return SimpleNamespace(
        models_dir=tmp_path / "stable_diffusion",
        available_models={"zimage-turbo": "Tongyi-MAI/Z-Image-Turbo", "sd-xl": "stabilityai/stable-diffusion-xl-base-1.0"},
        model_meta={},
        hidden_models=set(),
    )


def _stub_image_inspect(monkeypatch, *, hf_repo, files, has_model_index=False, unwired=None):
    from backend.services import user_image_models as uim

    listed = list(files)

    def fake(_url):
        return {
            "hf_repo": hf_repo,
            "revision": "main",
            "src": listed[0]["src"] if listed else None,
            "files": listed,
            "has_model_index": has_model_index,
            "gated": False,
            "truncated": False,
            "warnings": [],
            "matches": [],
            "unwired": unwired,
        }

    monkeypatch.setattr(uim, "preview_hf_url", fake)


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.services import user_image_models as uim
    monkeypatch.setattr(uim, "_CATALOG_PATH_OVERRIDE", tmp_path / "user_image_models.json")
    app = Flask(__name__)
    app.config.update({"TESTING": True})
    app.register_blueprint(batch_image_bp)
    monkeypatch.setattr(api, "service_available", True, raising=False)
    gen = _generator(tmp_path)
    wrapper = MagicMock()
    wrapper.image_generator = gen
    monkeypatch.setattr(api, "get_batch_image_generator", lambda: wrapper)
    return app.test_client()


def test_from_hf_rejects_non_hf(client):
    payload = client.post("/api/batch-image/models/from-hf", json={"url": "https://civitai.com/x"}).get_json()
    assert payload["success"] is False
    assert "Hugging Face" in ((payload.get("error") or {}).get("message") or "")


def test_from_hf_rejects_dataset(client):
    payload = client.post(
        "/api/batch-image/models/from-hf",
        json={"url": "https://huggingface.co/datasets/org/repo"},
    ).get_json()
    assert payload["success"] is False
    assert "dataset" in ((payload.get("error") or {}).get("message") or "").lower()


def test_user_add_and_delete(client, tmp_path, monkeypatch):
    from backend.services import user_image_models as uim
    _stub_image_inspect(
        monkeypatch,
        hf_repo="someone/zimage-lora",
        files=[{"src": "realism.safetensors", "size": 32_000_000}],
    )
    body = {
        "role": "lora",
        "family": "zimage",
        "hf_repo": "someone/zimage-lora",
        "files": [{"src": "realism.safetensors"}],
        "name": "API lora",
        "install": False,
    }
    payload = client.post("/api/batch-image/models/user", json=body).get_json()
    assert payload["success"], payload
    mid = payload["data"]["id"]
    try:
        assert mid.startswith("user-zimage-")
        assert payload["data"]["entry"]["role"] == "lora"
        assert payload["data"]["entry"]["files"][0]["size"] == 32_000_000
        assert payload["data"]["entry"]["size_gb"] == round(32_000_000 / (1024 ** 3), 3)
        forbidden = client.delete("/api/batch-image/models/user/zimage-turbo").get_json()
        assert forbidden["success"] is False
        gone = client.delete(f"/api/batch-image/models/user/{mid}").get_json()
        assert gone["success"] is True
    finally:
        try:
            wrapper = api.get_batch_image_generator()
            uim.remove_user_model(wrapper.image_generator, mid)
        except Exception:
            pass


def test_user_add_ignores_forged_model_index_when_url_inspected(client, monkeypatch):
    from backend.services import user_image_models as uim

    def fake_preview(_url):
        return {
            "hf_repo": "x/y",
            "revision": "main",
            "src": None,
            "files": [{"src": "merged.safetensors", "size": 100}],
            "has_model_index": False,
            "gated": False,
            "truncated": False,
            "warnings": [],
            "matches": [],
            "unwired": None,
        }

    monkeypatch.setattr(uim, "preview_hf_url", fake_preview)
    monkeypatch.setattr(api, "preview_hf_image_model", api.preview_hf_image_model)
    body = {
        "url": "https://huggingface.co/x/y",
        "role": "generation",
        "family": "zimage",
        "has_model_index": True,
        "files": [{"src": "merged.safetensors"}],
        "name": "Forged",
        "install": False,
    }
    payload = client.post("/api/batch-image/models/user", json=body).get_json()
    assert payload["success"] is False
    assert "diffusers" in ((payload.get("error") or {}).get("message") or "").lower()


def test_user_add_install_409_still_succeeds(client, monkeypatch):
    from backend.utils.response_utils import error_response
    from backend.services import user_image_models as uim
    _stub_image_inspect(
        monkeypatch,
        hf_repo="someone/zimage-lora",
        files=[{"src": "busy.safetensors", "size": 1000}],
    )
    monkeypatch.setattr(
        api, "_start_image_model_download",
        lambda _mid: error_response("Already downloading model: other", 409),
    )
    body = {
        "role": "lora",
        "family": "zimage",
        "hf_repo": "someone/zimage-lora",
        "files": [{"src": "busy.safetensors"}],
        "name": "While busy",
        "install": True,
    }
    payload = client.post("/api/batch-image/models/user", json=body).get_json()
    assert payload["success"], payload
    mid = payload["data"]["id"]
    try:
        assert payload["data"]["download"]["status"] == 409
    finally:
        try:
            wrapper = api.get_batch_image_generator()
            uim.remove_user_model(wrapper.image_generator, mid)
        except Exception:
            pass


def test_user_add_without_url_refuses_unwired(client, monkeypatch):
    _stub_image_inspect(
        monkeypatch,
        hf_repo="Qwen/Qwen-Image",
        files=[{"src": "model.safetensors", "size": 100}],
        unwired={"family": "qwen-image", "reason": "Qwen-Image is not wired yet."},
    )
    body = {
        "role": "generation",
        "family": "zimage",
        "hf_repo": "Qwen/Qwen-Image",
        "files": [{"src": "model.safetensors"}],
        "name": "Qwen",
        "install": False,
    }
    payload = client.post("/api/batch-image/models/user", json=body).get_json()
    assert payload["success"] is False
    assert "not wired" in ((payload.get("error") or {}).get("message") or "").lower()


def test_user_add_without_url_refuses_forged_snapshot(client, monkeypatch):
    _stub_image_inspect(
        monkeypatch,
        hf_repo="x/y",
        files=[{"src": "merged.safetensors", "size": 100}],
        has_model_index=False,
    )
    body = {
        "role": "generation",
        "family": "zimage",
        "hf_repo": "x/y",
        "has_model_index": True,
        "files": [{"src": "merged.safetensors"}],
        "name": "Forged no url",
        "install": False,
    }
    payload = client.post("/api/batch-image/models/user", json=body).get_json()
    assert payload["success"] is False
    assert "diffusers" in ((payload.get("error") or {}).get("message") or "").lower()


def test_list_includes_undownloaded_user_flux(client, monkeypatch):
    from backend.services import user_image_models as uim

    gen = api.get_batch_image_generator().image_generator
    gen.get_available_models = lambda probe_remote=True: {
        "user-flux-fp8": {
            "id": "user:user-flux-fp8",
            "downloaded": False,
            "availability": "downloadable",
            "selectable": True,
            "label": "Flux FP8",
            "description": "",
            "recommended": False,
            "user": True,
            "family": "flux",
            "kind": "comfy_files",
            "order": 1,
            "size_gb": 11.2,
        }
    }
    monkeypatch.setattr(uim, "catalog_rows", lambda _g: [])
    monkeypatch.setattr(uim, "family_choices", lambda: [])
    payload = client.get("/api/batch-image/models").get_json()
    assert payload["success"], payload
    ids = [m["id"] for m in payload["data"]["models"]]
    assert "user-flux-fp8" in ids
    assert "user-flux-fp8" not in [m["id"] for m in payload["data"]["unavailable_models"]]


def test_dir_bytes_counts_dest_including_incomplete(tmp_path):
    dest = tmp_path / "unet"
    dest.mkdir()
    (dest / "file.safetensors").write_bytes(b"x" * 100)
    (dest / "file.safetensors.incomplete").write_bytes(b"y" * 50)
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / "huge.incomplete").write_bytes(b"z" * 1_000_000)
    assert api._dir_bytes(dest) == 150
    assert api._dir_bytes(hub) == 1_000_000


def test_download_409_while_thread_still_marked_running(client, monkeypatch):
    monkeypatch.setattr(
        api, "_resolve_catalog_model",
        lambda _ref: ("zimage-turbo", "Tongyi-MAI/Z-Image-Turbo"),
    )
    gen = api.get_batch_image_generator().image_generator
    gen._is_model_downloaded = lambda _m: False
    with api.model_download_lock:
        api.model_download_status.update({
            "is_downloading": True,
            "current_model": "other/repo",
            "status": "failed",
            "updated_at": time.time() - 10_000,
            "epoch": 1,
        })
    try:
        payload = client.post(
            "/api/batch-image/models/download",
            json={"model_path": "zimage-turbo"},
        ).get_json()
        assert payload["success"] is False
        assert "Already downloading" in ((payload.get("error") or {}).get("message") or "")
    finally:
        with api.model_download_lock:
            api.model_download_status["is_downloading"] = False
