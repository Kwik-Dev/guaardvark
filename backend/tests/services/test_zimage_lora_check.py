"""Plain-words verdicts on files offered as Z-Image LoRAs, from key names alone."""

import json
import struct

from backend.services.zimage_lora_check import (
    NOT_SAFETENSORS,
    lora_file_problem,
    plain_load_error,
    zimage_lora_problem,
)


def test_loadable_layouts_pass():
    for keys in (
        ["diffusion_model.layers.0.attention.to_q.lora_A.weight",
         "diffusion_model.layers.0.attention.to_q.lora_B.weight"],
        ["lora_unet_layers_3_attention_qkv.lora_down.weight",
         "lora_unet_layers_3_attention_qkv.lora_up.weight",
         "lora_unet_layers_3_attention_qkv.alpha"],
        ["transformer.base_model.model.context_refiner.1.feed_forward.w2.lora_A.weight"],
    ):
        assert zimage_lora_problem(keys) is None, keys


def test_lokr_and_loha_are_named():
    assert "LoKr" in zimage_lora_problem(
        ["diffusion_model.layers.0.attention.to_q.lokr_w1",
         "diffusion_model.layers.0.attention.to_q.alpha"])
    assert "LoHa" in zimage_lora_problem(["lora_unet_layers_0_attention_to_q.hada_w1_a"])


def test_full_checkpoint_is_not_a_lora():
    problem = zimage_lora_problem(
        ["conditioner.embedders.0.transformer.text_model.final_layer_norm.weight",
         "first_stage_model.decoder.conv_in.weight"])
    assert "no LoRA weights" in problem


def test_other_model_adapters_are_named():
    assert "FLUX" in zimage_lora_problem(["lora_unet_double_blocks_0_img_mlp_0.lora_down.weight"])
    assert "Qwen-Image" in zimage_lora_problem(
        ["transformer_blocks.0.img_mlp.net.0.proj.lora_A.weight"])
    assert "SDXL" in zimage_lora_problem(
        ["lora_unet_input_blocks_4_1_proj_in.lora_down.weight"])
    assert "Wan" in zimage_lora_problem(["diffusion_model.blocks.0.self_attn.q.lora_A.weight"])
    assert "Krea 2" in zimage_lora_problem(["diffusion_model.blocks.0.attn.wq.lora_A.weight"])


def test_local_header_read(tmp_path):
    header = json.dumps({
        "__metadata__": {"format": "pt"},
        "layers.0.attention.to_q.lokr_w1": {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]},
    }).encode()
    path = tmp_path / "x.safetensors"
    path.write_bytes(struct.pack("<Q", len(header)) + header + b"\0\0")
    assert "LoKr" in lora_file_problem(path)
    assert lora_file_problem(tmp_path / "x.ckpt") == NOT_SAFETENSORS


def test_plain_load_error_drops_the_key_list():
    err = ValueError(
        "`state_dict` should be empty at this point but has state_dict.keys()=dict_keys("
        + str([f"layers.{i}.attention.to_out.0.alpha" for i in range(30)]) + ")"
    )
    msg = plain_load_error(err)
    assert "30 weights left unplaced" in msg
    assert "layers.3." not in msg
