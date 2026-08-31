"""
Apple Silicon detection for hardware_detector (Mac/MPS support, Tier 1).

Mock-based — no Apple hardware needed. Verifies that on arm64 Darwin we report
vendor=apple / accel=mps / unified_memory_gb, that Intel/Linux don't, and that the
RAM probe falls back to sysctl on macOS (no /proc/meminfo there).
"""

import subprocess
import types

import pytest

import backend.services.hardware_detector as hd


def _fake_sysctl(values):
    """Return a fake subprocess.run that answers sysctl -n <key> from `values`."""
    def _run(args, **kwargs):
        key = args[-1]
        out = values.get(key, "")
        return types.SimpleNamespace(returncode=0 if out else 1, stdout=out, stderr="")
    return _run


def test_apple_probe_reports_mps_and_unified_memory(monkeypatch):
    monkeypatch.setattr(hd.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hd.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(hd.subprocess, "run", _fake_sysctl({
        "machdep.cpu.brand_string": "Apple M2 Ultra",
        "hw.memsize": str(64 * 1024**3),
    }))
    gpu = hd.HardwareDetector()._probe_gpu_apple()
    assert gpu["vendor"] == "apple"
    assert gpu["accel"] == "mps"
    assert gpu["model"] == "Apple M2 Ultra"
    assert gpu["unified_memory_gb"] == 64.0


def test_apple_probe_none_on_intel_mac(monkeypatch):
    monkeypatch.setattr(hd.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hd.platform, "machine", lambda: "x86_64")  # Intel Mac -> no MPS
    assert hd.HardwareDetector()._probe_gpu_apple() is None


def test_apple_probe_none_on_linux(monkeypatch):
    monkeypatch.setattr(hd.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hd.platform, "machine", lambda: "x86_64")
    assert hd.HardwareDetector()._probe_gpu_apple() is None


def test_ram_probe_sysctl_fallback_on_mac(monkeypatch):
    # Force the /proc/meminfo path to fail, then macOS sysctl should answer.
    def _no_proc(*a, **k):
        raise OSError("no /proc/meminfo")
    monkeypatch.setattr("builtins.open", _no_proc)
    monkeypatch.setattr(hd.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hd.subprocess, "run", _fake_sysctl({"hw.memsize": str(32 * 1024**3)}))
    assert hd.HardwareDetector()._probe_ram() == {"total_gb": 32.0}
