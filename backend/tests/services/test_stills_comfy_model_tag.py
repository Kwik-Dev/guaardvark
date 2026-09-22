"""Stock FLUX routing follows the caller's tag; the ``comfyui`` selector does too.

Upstream #218 made ``_generate_comfy_flux`` pass the caller's model tag through
(``flux-dev`` reaches the FLUX-dev graph, plain ``flux`` the schnell GGUF branch).
The generic ``comfyui`` backend selector resolves an engine from the live server
and passes it as ``comfy_model``, so dispatch matches the family defaults it was
resolved from without reverting the stock behaviour.
"""
from pathlib import Path

import pytest

try:
    from backend.services import stills_pipeline as sp
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


class _RecordingGen:
    def __init__(self, *args, **kwargs):
        pass

    def generate_image(self, **kwargs):
        _RecordingGen.last = kwargs
        p = Path(self.__class__._out)
        p.write_bytes(b"x")
        return str(p)


def _run(monkeypatch, tmp_path, *, model, comfy_model=None):
    _RecordingGen._out = tmp_path / "out.png"
    monkeypatch.setattr(
        "backend.services.comfyui_image_generator.ComfyUIImageGenerator", _RecordingGen
    )
    sp._generate_comfy_flux(
        prompt="p", negative="", model=model, width=512, height=512, steps=28,
        guidance=3.5, seed=1, enhance_mode="none", output="", output_dir=None,
        comfy_model=comfy_model,
    )
    return _RecordingGen.last["model"]


def test_stock_flux_passes_caller_tag(monkeypatch, tmp_path):
    # Upstream #218: the caller's tag picks the graph, so flux-dev must reach the
    # FLUX-dev graph rather than the schnell GGUF branch.
    assert _run(monkeypatch, tmp_path, model="flux-dev") == "flux-dev"
    assert _run(monkeypatch, tmp_path, model="flux") == "flux"


def test_comfyui_selector_passes_resolved_engine_tag(monkeypatch, tmp_path):
    assert _run(
        monkeypatch, tmp_path, model="comfyui (flux-dev)", comfy_model="flux-dev"
    ) == "flux-dev"


# ── step resolution for the generic comfyui selector ──────────────────────────
# A static "comfyui" family row cannot carry the right step count for all three
# engines: 9 steps is Z-Image Turbo's recipe but badly under-resolves FLUX-dev
# (~28). run_stills_pipeline resolves the engine first and inherits that engine's
# real family defaults; a caller-set step count still wins.

def _capture(monkeypatch, attr):
    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return sp.StillResult(success=True, steps=kwargs["steps"])

    monkeypatch.setattr(sp, attr, fake)
    return seen


def test_comfyui_selector_flux_dev_uses_flux_steps(monkeypatch):
    seen = _capture(monkeypatch, "_generate_comfy_flux")
    monkeypatch.setattr(sp, "_comfyui_backend_choice", lambda: ("flux-dev", "flux-dev"))

    sp.run_stills_pipeline(["a lighthouse in fog"], model="comfyui", verbatim=True, output="none")

    assert seen["steps"] == 28
    assert seen["comfy_model"] == "flux-dev"
    assert seen["steps_explicit"] is False


def test_comfyui_selector_zimage_uses_zimage_steps(monkeypatch):
    seen = _capture(monkeypatch, "_generate_comfy_zimage")
    monkeypatch.setattr(sp, "_comfyui_backend_choice", lambda: ("zimage", "zimage"))

    sp.run_stills_pipeline(["a lighthouse in fog"], model="comfyui", verbatim=True, output="none")

    assert seen["steps"] == 9


def test_comfyui_selector_explicit_steps_win(monkeypatch):
    seen = _capture(monkeypatch, "_generate_comfy_flux")
    monkeypatch.setattr(sp, "_comfyui_backend_choice", lambda: ("flux-dev", "flux-dev"))

    sp.run_stills_pipeline(
        ["a lighthouse in fog"], model="comfyui", steps=12, steps_explicit=True,
        verbatim=True, output="none",
    )

    assert seen["steps"] == 12
    assert seen["steps_explicit"] is True
