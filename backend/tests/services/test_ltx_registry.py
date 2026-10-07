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


# ── LoRA scope: a LoRA names its models; an empty list offers it to none ──────

def test_every_shipped_lora_names_its_models_or_is_not_an_adapter():
    loras = [mid for mid, e in vmr.VIDEO_MODEL_REGISTRY.items() if e.get("type") == "lora"]
    assert loras
    for mid in loras:
        assert vmr._verify_lora_scope(mid, vmr.VIDEO_MODEL_REGISTRY[mid]) == [], mid


def test_ltx23_distilled_lora_is_not_offered():
    entry = vmr.VIDEO_MODEL_REGISTRY["ltx-distilled-lora"]
    assert entry["adapter"] is False
    assert entry["applies_to"] == []


def test_verify_registry_flags_a_lora_without_targets(monkeypatch):
    lora = {"name": "x", "type": "lora", "local_subdir": "loras",
            "files": [{"dst": "x.safetensors"}], "check_files": ["loras/x.safetensors"]}
    mid = "test-lora-no-scope"
    monkeypatch.setitem(vmr.VIDEO_MODEL_REGISTRY, mid, dict(lora))
    assert any(f"{mid}: LoRA must list" in p for p in vmr.verify_registry())

    monkeypatch.setitem(vmr.VIDEO_MODEL_REGISTRY, mid, dict(lora, applies_to=["no-such-model"]))
    assert any("unknown model 'no-such-model'" in p for p in vmr.verify_registry())

    monkeypatch.setitem(vmr.VIDEO_MODEL_REGISTRY, mid, dict(lora, applies_to=["ltx-vae"]))
    assert any("'ltx-vae', which is not a generation model" in p for p in vmr.verify_registry())

    monkeypatch.setitem(vmr.VIDEO_MODEL_REGISTRY, mid, dict(lora, applies_to=["wan22-5b"], adapter=False))
    assert any("adapter False but applies_to" in p for p in vmr.verify_registry())

    monkeypatch.setitem(vmr.VIDEO_MODEL_REGISTRY, mid, dict(lora, applies_to=[], adapter=False))
    assert vmr.verify_registry() == []


def _adapter_gen():
    from backend.services.comfyui_video_generator import ComfyUIVideoGenerator
    # Skip __init__ (it probes a live ComfyUI); the adapter check reads only the registry.
    return ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)


def test_adapter_check_refuses_a_lora_for_a_model_it_does_not_name(monkeypatch):
    from types import SimpleNamespace

    gen = _adapter_gen()
    for model in ("wan22-5b", "minimax-h3-int8", "ltx23-distilled-fp8"):
        out, err = gen._resolve_adapters(SimpleNamespace(adapters=["ltx-distilled-lora"]), model)
        assert out is None and "does not apply to this model" in err, model

    monkeypatch.setitem(vmr.VIDEO_MODEL_REGISTRY, "test-lora-empty", {
        "name": "Empty", "type": "lora", "applies_to": [],
        "files": [{"dst": "e.safetensors"}], "local_subdir": "loras"})
    out, err = gen._resolve_adapters(SimpleNamespace(adapters=["test-lora-empty"]), "wan22-5b")
    assert out is None and "Empty does not apply" in err


def test_adapter_check_accepts_a_named_model(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setitem(vmr.VIDEO_MODEL_REGISTRY, "test-lora-wan5b", {
        "name": "Style", "type": "lora", "applies_to": ["wan22-5b"],
        "files": [{"dst": "s.safetensors"}], "local_subdir": "loras"})
    monkeypatch.setattr(vmr, "is_model_installed", lambda mid: True)
    out, err = _adapter_gen()._resolve_adapters(
        SimpleNamespace(adapters=[{"id": "test-lora-wan5b", "strength": 0.6}]), "wan22-5b")
    assert err is None and out == [{"filename": "s.safetensors", "strength": 0.6}]
