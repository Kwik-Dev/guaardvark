"""Pipeline unload ordering tests.

2026-08-04 client box 2048² incident: _unload_pipeline_unlocked nulled every
pipeline component BEFORE calling remove_all_hooks(). Diffusers'
remove_all_hooks iterates pipeline.components and only acts on live
nn.Modules — with everything already None it removed ZERO hooks, so the
accelerate offload hooks kept their weights_map (the entire multi-GB CPU
state dict) across every "unload". These tests pin the ordering fix and the
removal of maybe_free_model_hooks (which RE-APPLIES offload hooks when any
hooks survive).
"""
from types import SimpleNamespace

import pytest

import backend.services.offline_image_generator as oig


@pytest.fixture
def gen():
    return oig.OfflineImageGenerator()


class _FakePipeline:
    """Records whether components were still alive when hooks were removed."""

    def __init__(self):
        self.unet = object()
        self.vae = object()
        self.text_encoder = object()
        self.transformer = object()
        self.components_alive_at_hook_removal = None
        self.maybe_free_called = False

    def remove_all_hooks(self):
        self.components_alive_at_hook_removal = all(
            getattr(self, attr) is not None
            for attr in ("unet", "vae", "text_encoder", "transformer")
        )

    def maybe_free_model_hooks(self):
        self.maybe_free_called = True

    def to(self, device):
        return self


def test_hooks_removed_while_components_alive(gen):
    fake = _FakePipeline()
    gen._pipeline = fake
    assert gen._unload_pipeline_unlocked() is True
    assert fake.components_alive_at_hook_removal is True, (
        "remove_all_hooks must run BEFORE component nulling — after nulling it "
        "iterates a dead components dict and detaches nothing (2026-08-04)"
    )


def test_maybe_free_model_hooks_never_called(gen):
    # After remove_all_hooks it's a no-op; on any path where hooks remain it
    # RE-APPLIES enable_model_cpu_offload — never call it during teardown.
    fake = _FakePipeline()
    gen._pipeline = fake
    gen._unload_pipeline_unlocked()
    assert fake.maybe_free_called is False


def test_components_nulled_after_unload(gen):
    fake = _FakePipeline()
    gen._pipeline = fake
    gen._unload_pipeline_unlocked()
    assert fake.unet is None and fake.vae is None
    assert gen._pipeline is None
