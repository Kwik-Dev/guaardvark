"""Photo edits render at the editing model's registry step counts and say what ran.

FLUX.1 Kontext declares a default on its registry entry and no floor: an edit,
inpaint or outpaint that names no count renders the default, and a count that
is given is used as given. Qwen-Image-Edit declares a floor: a lower count is
raised to it with a sentence in the result. ComfyUI is a stand-in: no GPU,
network or database.
"""
from __future__ import annotations

import contextlib

import pytest

from backend import config
from backend.services import comfyui_image_generator as cig
from backend.services import video_model_registry as vmr
from backend.tools import image_tools as it


@pytest.fixture
def comfy(monkeypatch, tmp_path):
    """ComfyUIImageGenerator with the network calls replaced; returns the queued workflows."""
    queued = []
    gen = cig.ComfyUIImageGenerator
    monkeypatch.setattr(gen, "_kontext_installed", lambda self: True)
    monkeypatch.setattr(gen, "qwen_edit_installed", lambda self: False)
    monkeypatch.setattr(gen, "_require_up", lambda self, message: None)
    monkeypatch.setattr(gen, "_edit_gpu_session",
                        staticmethod(lambda op_id, gpu_wait, **kw: contextlib.nullcontext()))
    monkeypatch.setattr(gen, "_upload_image_to_comfyui", lambda self, path: "src.png")
    monkeypatch.setattr(gen, "_queue", lambda self, workflow: queued.append(workflow) or "prompt-1")
    monkeypatch.setattr(gen, "_wait", lambda self, prompt_id, timeout=None: {})
    monkeypatch.setattr(gen, "_fetch_first_image", lambda self, outputs, output_path: output_path)
    monkeypatch.setattr(gen, "_run_edit_graph",
                        lambda self, workflow, output_path, **kw: queued.append(workflow) or output_path)
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setattr(it, "_chat_gpu_wait", lambda: None)
    return queued


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "photo.png"
    path.write_bytes(b"png")
    return str(path)


def _sampler_steps(workflow: dict) -> int:
    return next(n["inputs"]["steps"] for n in workflow.values() if n.get("class_type") == "KSampler")


def test_kontext_declares_a_default_and_no_floor_on_its_registry_entry():
    entry = vmr.VIDEO_MODEL_REGISTRY["flux-kontext-dev"]
    assert entry["default_steps"] == cig.KONTEXT_DEFAULT_STEPS > 20
    assert "min_steps" not in entry
    assert not [p for p in vmr.verify_registry() if p.startswith("flux-kontext-dev:")]


def test_edit_steps_uses_a_given_count_and_raises_only_to_a_declared_floor():
    # No floor declared: the default when nothing is given, otherwise the count as given.
    assert cig.edit_steps(None, default=28) == (28, None)
    assert cig.edit_steps(0, default=28) == (28, None)
    assert cig.edit_steps("junk", default=28) == (28, None)
    assert cig.edit_steps(4, default=28) == (4, None)
    assert cig.edit_steps(40, default=28) == (40, None)
    # A declared floor raises a lower count and says so; the default never renders below it.
    steps, notice = cig.edit_steps(10, default=20, floor=20, label="Qwen-Image-Edit")
    assert steps == 20 and notice == "Qwen-Image-Edit needs at least 20 steps; raised 10 to 20."
    assert cig.edit_steps(30, default=20, floor=20, label="x") == (30, None)
    assert cig.edit_steps(None, default=20, floor=28, label="x") == (28, None)


@pytest.mark.parametrize("asked", [None, 4, 20, 40])
def test_a_kontext_edit_renders_the_default_or_the_count_it_was_given(comfy, photo, tmp_path, asked):
    gen = cig.ComfyUIImageGenerator()
    gen.edit_image(image_path=photo, instruction="x", output_path=str(tmp_path / "o.png"), steps=asked)
    rendered = cig.KONTEXT_DEFAULT_STEPS if asked is None else asked
    assert _sampler_steps(comfy[-1]) == rendered == gen.last_steps
    assert gen.last_steps_notice is None


def test_inpaint_and_outpaint_render_kontext_at_its_default_when_no_count_is_given(comfy, photo):
    res = it.InpaintImageTool().execute(instruction="remove the cup", image=photo)
    assert res.success and _sampler_steps(comfy[-1]) == cig.KONTEXT_DEFAULT_STEPS
    assert res.metadata["steps"] == cig.KONTEXT_DEFAULT_STEPS and res.metadata["steps_notice"] is None
    assert f"Steps: {cig.KONTEXT_DEFAULT_STEPS}" in res.output and "raised" not in res.output

    res = it.OutpaintImageTool().execute(image=photo)
    assert res.success and _sampler_steps(comfy[-1]) == cig.KONTEXT_DEFAULT_STEPS
    assert res.metadata["steps"] == cig.KONTEXT_DEFAULT_STEPS


def test_a_count_given_to_a_kontext_tool_call_is_used_as_given(comfy, photo):
    res = it.EditImageTool().execute(instruction="make it night", image=photo, steps=4)
    assert res.success and _sampler_steps(comfy[-1]) == 4
    assert res.metadata["steps"] == 4 and res.metadata["steps_notice"] is None
    assert "Steps: 4" in res.output and "raised" not in res.output

    res = it.InpaintImageTool().execute(instruction="remove the cup", image=photo, steps=20)
    assert _sampler_steps(comfy[-1]) == 20 and "raised" not in res.output
    res = it.InpaintImageTool().execute(instruction="remove the cup", image=photo, steps=12)
    assert _sampler_steps(comfy[-1]) == 12 and res.metadata["steps"] == 12


def test_outpaint_on_kontext_raises_a_given_count_to_at_least_twenty(comfy, photo):
    res = it.OutpaintImageTool().execute(image=photo, steps=12)
    assert res.success and _sampler_steps(comfy[-1]) == 20 and res.metadata["steps"] == 20
    assert "Steps: 20" in res.output
    res = it.OutpaintImageTool().execute(image=photo, steps=30)
    assert _sampler_steps(comfy[-1]) == 30
    # No count is not a low count: the model's default renders.
    res = it.OutpaintImageTool().execute(image=photo)
    assert _sampler_steps(comfy[-1]) == cig.KONTEXT_DEFAULT_STEPS
    assert "at least 20" in it.OutpaintImageTool.parameters["steps"].description


def test_qwen_keeps_its_own_floor_and_reports_it(comfy, photo, monkeypatch, tmp_path):
    monkeypatch.setattr(cig.ComfyUIImageGenerator, "qwen_edit_installed", lambda self: True)
    floor = cig.QWEN_EDIT_MIN_STEPS
    gen = cig.ComfyUIImageGenerator()
    gen.edit_image_qwen(image_paths=[photo], instruction="x", output_path=str(tmp_path / "q.png"), steps=floor - 10)
    assert _sampler_steps(comfy[-1]) == floor and f"raised {floor - 10} to {floor}" in gen.last_steps_notice

    # inpaint names no count: Qwen renders its floor. edit_image publishes 28, which stands.
    res = it.InpaintImageTool().execute(instruction="remove the cup", image=photo)
    assert res.success and _sampler_steps(comfy[-1]) == floor and res.metadata["backend"] == "qwen"
    res = it.EditImageTool().execute(instruction="make it night", image=photo)
    assert res.success and _sampler_steps(comfy[-1]) == 28
