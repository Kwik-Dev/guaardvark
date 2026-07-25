"""Wan CLIP/TE residency: CPU device on consumer VRAM (quality-preserving).

Locks the architecture fix for the ComfyUI log thrash case: UMT5 must not
stack on GPU with the ~10GB UNet on 16GB cards. Same weights; device=cpu only.
"""
from __future__ import annotations

import os

import pytest

from backend.services.comfyui_video_generator import ComfyUIVideoGenerator


@pytest.fixture(autouse=True)
def _clear_clip_env(monkeypatch):
    monkeypatch.delenv("GUAARDVARK_WAN_CLIP_DEVICE", raising=False)


def test_wan_clip_device_cpu_on_16gb():
    assert ComfyUIVideoGenerator._wan_clip_device(total_vram_mb=15937) == "cpu"


def test_wan_clip_device_cpu_on_20gb_boundary():
    assert ComfyUIVideoGenerator._wan_clip_device(total_vram_mb=20 * 1024) == "cpu"


def test_wan_clip_device_default_on_24gb():
    assert ComfyUIVideoGenerator._wan_clip_device(total_vram_mb=24 * 1024) == "default"


def test_wan_clip_device_cpu_when_probe_unknown():
    # Fail-safe: unknown total prefers CPU (safe on consumer; quality-neutral).
    assert ComfyUIVideoGenerator._wan_clip_device(total_vram_mb=None) == "cpu"
    assert ComfyUIVideoGenerator._wan_clip_device(total_vram_mb=0) == "cpu"


def test_wan_clip_device_env_override(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_WAN_CLIP_DEVICE", "default")
    assert ComfyUIVideoGenerator._wan_clip_device(total_vram_mb=15937) == "default"
    monkeypatch.setenv("GUAARDVARK_WAN_CLIP_DEVICE", "cpu")
    assert ComfyUIVideoGenerator._wan_clip_device(total_vram_mb=48 * 1024) == "cpu"


def test_t2v_workflow_sets_clip_device_cpu(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_WAN_CLIP_DEVICE", "cpu")
    gen = ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)
    # Minimal map so builder does not need a live Comfy install.
    gen.WAN22_MODELS = {
        "wan22-14b": {
            "unet_high": "hn.gguf",
            "unet_low": "ln.gguf",
            "clip": "umt5.safetensors",
            "vae": "wan_vae.safetensors",
        }
    }
    wf = ComfyUIVideoGenerator._create_wan22_t2v_workflow(
        gen,
        prompt="a cat walks",
        model_key="wan22-14b",
        num_frames=17,
        num_inference_steps=10,
        width=832,
        height=480,
    )
    clip = wf["3"]
    assert clip["class_type"] == "CLIPLoader"
    assert clip["inputs"]["device"] == "cpu"
    assert clip["inputs"]["type"] == "wan"


def test_i2v_workflow_sets_clip_device_cpu(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_WAN_CLIP_DEVICE", "cpu")
    gen = ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)
    gen.WAN22_MODELS = {
        "wan22-14b-i2v": {
            "unet_high": "hn.gguf",
            "unet_low": "ln.gguf",
            "clip": "umt5.safetensors",
            "vae": "wan_vae.safetensors",
        }
    }
    wf = ComfyUIVideoGenerator._create_wan22_i2v_workflow(
        gen,
        image_filename="start.png",
        prompt="a cat walks",
        model_key="wan22-14b-i2v",
        num_frames=17,
        num_inference_steps=10,
    )
    assert wf["3"]["inputs"]["device"] == "cpu"
