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
