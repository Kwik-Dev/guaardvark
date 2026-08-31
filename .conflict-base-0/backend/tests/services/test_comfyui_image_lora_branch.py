"""Keyframe LoRA branch-selection guard (subject-16 model-collapse fix).

The character LoRAs this app trains are SDXL. Branch selection in
ComfyUIImageGenerator._build_workflow is by model STRING, which historically let
a stray model name silently drop the LoRA (the flux-schnell branch has no LoRA
nodes; the flux-dev branch expects FLUX-format LoRAs). These tests pin the
guarantee: whenever LoRAs are present the workflow MUST contain a LoRA loader
node, regardless of the requested model string.
"""
import pytest

try:
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


def _class_types(workflow: dict) -> set[str]:
    return {node.get("class_type") for node in workflow.values()}


def _build(model: str, lora_names: list[str]) -> dict:
    gen = ComfyUIImageGenerator(model=model)
    return gen._build_workflow(
        prompt="sage_harlow, cinematic portrait", negative="",
        lora_names=lora_names, width=1024, height=1024,
        seed=1, steps=28, cfg=1.0, model=model,
    )


def test_flux_schnell_with_loras_does_not_drop_them():
    # The flux-schnell branch has no LoRA nodes; the guard must reroute to SDXL
    # so the LoRA is actually applied.
    wf = _build("flux-schnell", ["sage_harlow_v3.safetensors"])
    types = _class_types(wf)
    assert "LoraLoader" in types, "SDXL LoRA chain expected after guard reroute"
    assert "DiffusersLoader" in types, "should be on the SDXL branch"


def test_flux_dev_with_loras_reroutes_to_sdxl():
    # flux-dev would load an SDXL LoRA in the wrong format — guard reroutes it.
    wf = _build("flux-dev", ["sage_harlow_v3.safetensors"])
    types = _class_types(wf)
    assert "LoraLoader" in types
    assert "DiffusersLoader" in types


def test_sdxl_with_loras_builds_lora_chain():
    wf = _build("sdxl", ["sage_harlow_v3.safetensors"])
    types = _class_types(wf)
    assert "LoraLoader" in types
    assert "DiffusersLoader" in types


def test_flux_without_loras_keeps_flux_branch():
    # No LoRAs → flux branch is fine (plain stylistic still), guard is inert.
    wf = _build("flux-schnell", [])
    types = _class_types(wf)
    assert "UnetLoaderGGUF" in types
    assert "LoraLoader" not in types
