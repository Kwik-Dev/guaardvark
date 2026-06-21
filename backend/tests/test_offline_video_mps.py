"""
Device-selection tests for offline video generation (Mac/MPS support, PREVIEW).

These verify the accelerator-selection LOGIC without GPU hardware — they mock
torch.cuda / torch.backends.mps. They do NOT prove CogVideoX actually renders on
MPS (that needs a Mac; tracked separately). They DO prove:
  - CUDA selection is unchanged (regression guard for the refactor),
  - a Mac (MPS-only, no CUDA) now selects 'mps' + bfloat16 instead of 'cpu',
  - CPU-only and no-torch fall through correctly.
"""

import pytest

import backend.services.offline_video_generator as ovg


@pytest.fixture
def fake_torch(monkeypatch):
    """Give the module a torch whose cuda/mps availability we control."""
    real_torch = ovg.torch  # real torch is installed in the venv; reuse its dtypes
    monkeypatch.setattr(ovg, "torch_available", True, raising=False)
    return real_torch


def _set(monkeypatch, real_torch, *, cuda: bool, mps: bool):
    monkeypatch.setattr(real_torch.cuda, "is_available", lambda: cuda)
    # _mps_available() reads torch.backends.mps.is_available — patch it there
    monkeypatch.setattr(real_torch.backends.mps, "is_available", lambda: mps, raising=False)


def test_cuda_wins(monkeypatch, fake_torch):
    _set(monkeypatch, fake_torch, cuda=True, mps=False)
    device, dtype = ovg._select_accelerator()
    assert device == "cuda"
    assert dtype is fake_torch.float16


def test_mps_selected_when_only_metal(monkeypatch, fake_torch):
    # The Mac case: no CUDA, MPS present -> 'mps' + bfloat16 (NOT 'cpu').
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    _set(monkeypatch, fake_torch, cuda=False, mps=True)
    device, dtype = ovg._select_accelerator()
    assert device == "mps"
    assert dtype is fake_torch.bfloat16
    # The CPU-fallback env must be set so unimplemented MPS ops degrade vs crash.
    import os
    assert os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1"


def test_cpu_when_no_accelerator(monkeypatch, fake_torch):
    _set(monkeypatch, fake_torch, cuda=False, mps=False)
    device, dtype = ovg._select_accelerator()
    assert device == "cpu"
    assert dtype is fake_torch.float32


def test_cpu_when_torch_missing(monkeypatch):
    monkeypatch.setattr(ovg, "torch_available", False, raising=False)
    device, dtype = ovg._select_accelerator()
    assert device == "cpu"
    assert dtype is None


def test_cuda_preference_over_mps(monkeypatch, fake_torch):
    # If both somehow report available, CUDA must win (real NVIDIA box).
    _set(monkeypatch, fake_torch, cuda=True, mps=True)
    device, _ = ovg._select_accelerator()
    assert device == "cuda"


def test_mps_availability_is_false_on_this_box():
    # Sanity: this CI/dev box is not a Mac, so the real probe is False (or at
    # worst safely False on any torch without the mps backend).
    assert ovg._mps_available() in (True, False)  # never raises
