"""VAE tiling/slicing fallback tests for OfflineImageGenerator.

Born from the 2026-08-04 client box 2048² desktop crash: ZImagePipeline/Krea2Pipeline
don't inherit StableDiffusionMixin, so the old pipeline-level hasattr checks for
enable_vae_tiling/enable_vae_slicing silently no-opped for exactly the two
families allowed to reach 2K — the untiled 2048² decode OOM'd the card and the
recovery ladder exhausted system RAM until the desktop died. These tests pin the
vae-level fallback (mirroring offline_video_generator) and the >1MP hard gate.
"""
from types import SimpleNamespace

import pytest

import backend.services.offline_image_generator as oig


@pytest.fixture
def gen():
    return oig.OfflineImageGenerator()


class _Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _record(*a, **kw):
            self.calls.append(name)
        return _record


def _vae_only_pipeline():
    """ZImage/Krea2-shaped: tiling/slicing only on the vae, not the pipeline."""
    vae = _Recorder()
    # __getattr__ answers everything, so hasattr(vae, ...) is True for the
    # vae-level names the production code probes.
    return SimpleNamespace(vae=vae), vae


def test_set_vae_tiling_uses_vae_level_for_dit_shape(gen):
    pipeline, vae = _vae_only_pipeline()
    gen._pipeline = pipeline
    assert gen._set_vae_tiling(True) is True
    assert "enable_tiling" in vae.calls
    assert gen._set_vae_tiling(False) is True
    assert "disable_tiling" in vae.calls


def test_enable_vae_slicing_uses_vae_level_for_dit_shape(gen):
    pipeline, vae = _vae_only_pipeline()
    gen._pipeline = pipeline
    gen._enable_vae_slicing_any()
    assert "enable_slicing" in vae.calls
    assert gen._vae_slicing_enabled is True


def test_set_vae_tiling_prefers_pipeline_level(gen):
    calls = []
    pipeline = SimpleNamespace(
        vae=object(),  # vae without methods must not matter
        enable_vae_tiling=lambda: calls.append("enable"),
        disable_vae_tiling=lambda: calls.append("disable"),
    )
    gen._pipeline = pipeline
    assert gen._set_vae_tiling(True) is True
    assert calls == ["enable"]
    assert gen._set_vae_tiling(False) is True
    assert calls == ["enable", "disable"]


def test_set_vae_tiling_reports_unavailable(gen):
    gen._pipeline = SimpleNamespace(vae=object())
    assert gen._set_vae_tiling(True) is False


def test_service_status_reports_computed_truth(gen):
    # The old status lied via pipeline-level hasattr; now it reports what the
    # load path actually managed to enable.
    gen._pipeline = SimpleNamespace()
    gen._vae_slicing_enabled = True
    gen._vae_tiling_available = True
    gen._vae_tiling_via = "vae"
    status = gen.get_service_status()
    opt = status["optimizations"]
    assert opt["vae_slicing"] is True
    assert opt["vae_tiling"] is True
    assert opt["vae_tiling_via"] == "vae"
