"""PEFT → Diffusers key remapping for Z-Image character LoRAs."""

from backend.services.offline_image_generator import normalize_zimage_lora_state_dict


def test_strips_transformer_base_model_model_prefix():
    raw = {
        "transformer.base_model.model.layers.0.attention.to_q.lora_A.weight": 1,
        "transformer.base_model.model.context_refiner.0.attention.to_k.lora_B.weight": 2,
    }
    out = normalize_zimage_lora_state_dict(raw)
    assert out == {
        "transformer.layers.0.attention.to_q.lora_A.weight": 1,
        "transformer.context_refiner.0.attention.to_k.lora_B.weight": 2,
    }


def test_strips_bare_peft_prefix_and_adds_transformer():
    raw = {"base_model.model.layers.3.attention.to_v.lora_A.weight": 9}
    out = normalize_zimage_lora_state_dict(raw)
    assert list(out) == ["transformer.layers.3.attention.to_v.lora_A.weight"]


def test_already_clean_keys_unchanged():
    raw = {"transformer.layers.1.attention.to_out.0.lora_A.weight": 3}
    assert normalize_zimage_lora_state_dict(raw) == raw


def test_splits_comfyui_fused_qkv_and_renames_out():
    import torch

    a = torch.randn(4, 12)
    b = torch.randn(36, 4)
    alpha = torch.tensor(2.0)
    raw = {
        "diffusion_model.layers.0.attention.qkv.lora_A.weight": a,
        "diffusion_model.layers.0.attention.qkv.lora_B.weight": b,
        "diffusion_model.layers.0.attention.qkv.alpha": alpha,
        "diffusion_model.layers.0.attention.out.lora_A.weight": a,
        "diffusion_model.layers.0.attention.out.alpha": alpha,
        "diffusion_model.layers.0.feed_forward.w1.lora_A.weight": a,
    }
    out = normalize_zimage_lora_state_dict(raw)
    prefix = "diffusion_model.layers.0."
    assert set(out) == {
        *(f"{prefix}attention.to_{p}.{s}" for p in "qkv"
          for s in ("lora_A.weight", "lora_B.weight", "alpha")),
        f"{prefix}attention.to_out.0.lora_A.weight",
        f"{prefix}attention.to_out.0.alpha",
        f"{prefix}feed_forward.w1.lora_A.weight",
    }
    for i, p in enumerate("qkv"):
        assert torch.equal(out[f"{prefix}attention.to_{p}.lora_B.weight"], b[i * 12:(i + 1) * 12])
        assert out[f"{prefix}attention.to_{p}.lora_A.weight"] is a
        assert out[f"{prefix}attention.to_{p}.alpha"] is alpha


def test_kohya_fused_keys_keep_underscore_spelling():
    import torch

    raw = {
        "lora_unet_context_refiner_1_attention_qkv.lora_up.weight": torch.zeros(9, 2),
        "lora_unet_context_refiner_1_attention_out.alpha": torch.tensor(1.0),
    }
    assert set(normalize_zimage_lora_state_dict(raw)) == {
        "lora_unet_context_refiner_1_attention_to_q.lora_up.weight",
        "lora_unet_context_refiner_1_attention_to_k.lora_up.weight",
        "lora_unet_context_refiner_1_attention_to_v.lora_up.weight",
        "lora_unet_context_refiner_1_attention_to_out_0.alpha",
    }


def test_fused_qkv_left_alone_next_to_split_keys():
    raw = {
        "diffusion_model.layers.2.attention.qkv.lora_A.weight": 1,
        "diffusion_model.layers.2.attention.to_q.lora_A.weight": 2,
    }
    assert normalize_zimage_lora_state_dict(raw) == raw
