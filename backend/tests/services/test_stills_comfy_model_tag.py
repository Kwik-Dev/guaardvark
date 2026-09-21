"""The generic ``comfyui`` selector must not silently change stock FLUX routing.

``_generate_comfy_flux`` used to hard-code ``model="flux"`` in the ComfyUI call,
so a stock FLUX still rendered on the schnell GGUF branch regardless of the
requested model id. The fix keeps that default and lets only the explicit
``comfyui`` backend selector pass a resolved engine tag.
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


def test_stock_flux_keeps_historic_flux_tag(monkeypatch, tmp_path):
    # A request for flux-dev must still render on the schnell branch as before.
    assert _run(monkeypatch, tmp_path, model="flux-dev") == "flux"


def test_comfyui_selector_passes_resolved_engine_tag(monkeypatch, tmp_path):
    assert _run(
        monkeypatch, tmp_path, model="comfyui (flux-dev)", comfy_model="flux-dev"
    ) == "flux-dev"
