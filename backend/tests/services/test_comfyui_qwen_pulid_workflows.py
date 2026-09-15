"""Qwen-Image-Edit and PuLID-FLUX Comfy graphs — class types and sampler floor."""
import pytest

try:
    from backend.services.comfyui_image_generator import (
        ComfyUIImageGenerator,
        QWEN_EDIT_MIN_STEPS,
    )
except Exception:  # pragma: no cover
    pytest.skip("Backend modules not available", allow_module_level=True)


def _types(wf: dict) -> set[str]:
    return {node.get("class_type") for node in wf.values()}


def test_qwen_edit_workflow_official_nodes_and_min_steps():
    gen = ComfyUIImageGenerator()
    wf = gen._build_qwen_edit_workflow(
        src_names=["face.png"], instruction="make it night",
        steps=4, cfg=2.5, seed=1,
    )
    types = _types(wf)
    assert "UNETLoader" in types
    assert "CLIPLoader" in types
    assert wf["clip"]["inputs"]["type"] == "qwen_image"
    assert "TextEncodeQwenImageEditPlus" in types
    assert "CFGNorm" in types
    assert "ModelSamplingAuraFlow" in types
    assert "ImagePadForOutpaint" not in types
    assert wf["sampler"]["inputs"]["steps"] == QWEN_EDIT_MIN_STEPS
    assert wf["sampler"]["inputs"]["steps"] >= 20
    assert "image2" not in wf["pos"]["inputs"]


def test_qwen_edit_workflow_pad_and_extra_refs():
    gen = ComfyUIImageGenerator()
    wf = gen._build_qwen_edit_workflow(
        src_names=["a.png", "b.png", "c.png"],
        instruction="continue the forest",
        steps=20, cfg=2.5, seed=1,
        pad={"left": 256, "right": 0, "top": 0, "bottom": 0, "feathering": 40},
    )
    assert wf["pad"]["class_type"] == "ImagePadForOutpaint"
    assert wf["pad"]["inputs"]["left"] == 256
    assert "image2" in wf["pos"]["inputs"]
    assert "image3" in wf["pos"]["inputs"]


def test_pulid_workflow_has_apply_node():
    gen = ComfyUIImageGenerator()
    wf = gen._build_pulid_workflow(
        src_image_name="face.png", prompt="a 1940s detective in the rain",
        width=768, height=1024, steps=4, seed=1,
    )
    types = _types(wf)
    assert "ApplyPulidFlux" in types
    assert "PulidFluxModelLoader" in types
    assert "PulidFluxInsightFaceLoader" in types
    assert wf["apply"]["inputs"]["unique_id"] == "pulid_apply"
    assert wf["sampler"]["inputs"]["steps"] >= 20
