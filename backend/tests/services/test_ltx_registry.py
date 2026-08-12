"""LTX-2.3 vs LTX-2.5 registry contract (loader map + gated-error copy)."""
import os

from backend.services import video_model_registry as vmr


def test_verify_registry_is_clean():
    assert vmr.verify_registry() == []


def test_ltx23_map_has_projection_not_upscaler():
    m = vmr.ltx_comfyui_map()["ltx23-distilled-fp8"]
    assert m["unet"]
    assert m["clip"]
    assert m["text_projection"]
    assert m["vae"]
    assert m["audio_vae"]
    assert "upscale_model" not in m


def test_ltx25_map_has_upscaler_not_projection():
    m = vmr.ltx_comfyui_map()["ltx25-distilled-int8"]
    assert m["unet"] == "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"
    assert m["clip"] == "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"
    assert m["vae"] == "ltx-2.5-video-vae-bf16.safetensors"
    assert m["audio_vae"] == "ltx-2.5-audio-vae-bf16.safetensors"
    assert m["upscale_model"] == "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"
    assert "text_projection" not in m


def test_ltx25_requires_only_new_companions():
    req = vmr.VIDEO_MODEL_REGISTRY["ltx25-distilled-int8"]["requires"]
    assert req == [
        "ltx25-gemma4-int8",
        "ltx25-vae",
        "ltx25-audio-vae",
        "ltx25-spatial-upscaler",
    ]
    for dep in req:
        assert dep in vmr.VIDEO_MODEL_REGISTRY
        assert vmr.VIDEO_MODEL_REGISTRY[dep]["hf_repo"] == "Lightricks/LTX-2.5"


def test_ltx25_does_not_reuse_23_companions():
    req = set(vmr.VIDEO_MODEL_REGISTRY["ltx25-distilled-int8"]["requires"])
    assert "ltx-gemma-fp4" not in req
    assert "ltx-text-projection" not in req
    assert "ltx-vae" not in req
    assert "ltx-audio-vae" not in req


def test_is_ltx25_model():
    assert vmr.is_ltx25_model("ltx25-distilled-int8") is True
    assert vmr.is_ltx25_model("ltx23-distilled-fp8") is False


def test_classify_hf_download_error_passthrough():
    assert "disk full" in vmr.classify_hf_download_error(RuntimeError("disk full"))


def test_classify_hf_download_error_needs_token(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    msg = vmr.classify_hf_download_error(
        RuntimeError("401 Client Error: Unauthorized"),
        repo_id="Lightricks/LTX-2.5",
    )
    assert "HF_TOKEN" in msg
    assert "Agree" not in msg


def test_classify_hf_download_error_needs_licence(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    msg = vmr.classify_hf_download_error(
        RuntimeError("403 Client Error: gated repo"),
        repo_id="Lightricks/LTX-2.5",
    )
    assert "Agree and access" in msg
    assert "https://huggingface.co/Lightricks/LTX-2.5" in msg
    assert os.environ.get("HF_TOKEN") == "hf_test"
