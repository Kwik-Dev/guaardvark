"""edit_image and inpaint_image refuse what they cannot do instead of reporting success.

Without an editing pack 'auto' is refused with the pack to install (img2img at
an edit's strength returns a near copy of the photo). A reference image that
cannot be read, or that the chosen backend would ignore, is refused too. The
renderers are stand-ins: no GPU, network or database.
"""
from __future__ import annotations

import pytest

from backend import config
from backend.services import comfyui_image_generator as cig
from backend.services.agent_tools import ToolResult
from backend.tools import image_tools as it


@pytest.fixture
def studio(monkeypatch, tmp_path):
    """Which packs are installed, and which renderer each edit reached."""
    state = {"qwen": False, "kontext": False, "ran": []}
    gen = cig.ComfyUIImageGenerator
    monkeypatch.setattr(gen, "qwen_edit_installed", lambda self: state["qwen"])
    monkeypatch.setattr(gen, "_kontext_installed", lambda self: state["kontext"])
    monkeypatch.setattr(gen, "edit_image_qwen",
                        lambda self, **kw: state["ran"].append(("qwen", len(kw["image_paths"]))))
    monkeypatch.setattr(gen, "edit_image", lambda self, **kw: state["ran"].append(("kontext", 1)))
    monkeypatch.setattr(
        it.EditImageTool, "_edit_via_img2img",
        lambda self, **kw: state["ran"].append(("img2img", kw["model"])) or ToolResult(
            success=True, output="img2img ran", metadata={"model": kw["model"]}))
    monkeypatch.setattr(it, "_chat_gpu_wait", lambda: None)
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "outputs"))
    for name in ("photo.png", "second.png"):
        (tmp_path / name).write_bytes(b"png")
    state["photo"], state["second"] = str(tmp_path / "photo.png"), str(tmp_path / "second.png")
    state["missing"] = str(tmp_path / "missing.png")
    return state


def test_auto_without_a_pack_is_refused_with_what_to_install(studio):
    assert it.EditImageTool._pick_edit_backend("auto") is None
    res = it.InpaintImageTool().execute(instruction="remove the cup", image=studio["photo"])
    assert not res.success and res.error.startswith("Inpainting needs Qwen-Image-Edit or FLUX.1 Kontext")
    assert "Manage Image Models" in res.error
    res = it.EditImageTool().execute(instruction="make it night", image=studio["photo"])
    assert not res.success and res.error.startswith("Image editing needs Qwen-Image-Edit or FLUX.1 Kontext")
    assert studio["ran"] == []


def test_img2img_runs_only_for_an_image_model_named_to_edit_image(studio):
    res = it.EditImageTool().execute(instruction="x", image=studio["photo"], model="sd-xl")
    assert res.success and res.metadata["backend"] == "img2img" and studio["ran"] == [("img2img", "sd-xl")]
    # inpaint_image has no img2img form: a model name chat passes from its setting means 'auto'.
    res = it.InpaintImageTool().execute(instruction="x", image=studio["photo"], model="sd-xl")
    assert not res.success and "Inpainting needs" in res.error and len(studio["ran"]) == 1
    studio["kontext"] = True
    res = it.InpaintImageTool().execute(instruction="x", image=studio["photo"], model="sd-xl")
    assert res.success and res.metadata["backend"] == "kontext" and res.metadata["model"] == "auto"
    assert studio["ran"][-1] == ("kontext", 1)


def test_a_reference_that_cannot_be_read_is_refused_by_name(studio):
    studio["qwen"] = True
    res = it.EditImageTool().execute(instruction="x", image=studio["photo"], reference_image_2=studio["missing"])
    assert not res.success and res.error.startswith("reference_image_2 not found")
    res = it.EditImageTool().execute(instruction="x", image=studio["photo"],
                                     reference_image_3="/api/batch-video/video/B/clip.mp4")
    assert not res.success and res.error.startswith("reference_image_3 ")
    assert studio["ran"] == []
    # Blank strings are what an omitted reference looks like.
    res = it.EditImageTool().execute(instruction="x", image=studio["photo"], reference_image_2=" ",
                                     reference_image_3="")
    assert res.success and studio["ran"] == [("qwen", 1)]


def test_references_are_refused_on_a_backend_that_edits_one_image(studio):
    args = dict(instruction="put them side by side", image=studio["photo"], reference_image_2=studio["second"])
    studio["kontext"] = True
    res = it.EditImageTool().execute(**args)
    assert not res.success and "only used by Qwen-Image-Edit" in res.error and "FLUX.1 Kontext" in res.error
    res = it.EditImageTool().execute(model="sd-xl", **args)
    assert not res.success and "img2img with 'sd-xl'" in res.error
    assert studio["ran"] == []

    studio["qwen"] = True
    res = it.EditImageTool().execute(reference_image_3=studio["second"], **args)
    assert res.success and studio["ran"] == [("qwen", 3)]
    # Naming Kontext with Qwen installed is still a one-image edit.
    res = it.EditImageTool().execute(model="kontext", **args)
    assert not res.success and "FLUX.1 Kontext" in res.error


def test_the_model_description_states_the_published_default():
    model = it.EditImageTool.parameters["model"]
    assert model.default == "auto"
    assert "Default 'auto'" in model.description and "Default follows" not in model.description
    assert "refused" in it.EditImageTool.parameters["reference_image_2"].description
    assert "refuses" in it.InpaintImageTool.description
