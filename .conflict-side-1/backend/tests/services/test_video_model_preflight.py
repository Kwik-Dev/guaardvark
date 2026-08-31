import pytest

from backend.services import video_model_registry as vmr


def test_preflight_unknown_model():
    ok, err = vmr.preflight_video_model("not-a-model")
    assert ok is False
    assert "Unknown" in err


def test_preflight_wan_requires_install(monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda _m: False)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: True)
    ok, err = vmr.preflight_video_model("wan22-5b")
    assert ok is False
    assert "not installed" in err.lower()


def test_preflight_wan_requires_comfy(monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda _m: True)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: False)
    ok, err = vmr.preflight_video_model("wan22-5b")
    assert ok is False
    assert "comfyui" in err.lower()


def test_preflight_wan_ok(monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda _m: True)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: True)
    ok, err = vmr.preflight_video_model("wan22-5b")
    assert ok is True
    assert err == ""


def test_default_models_are_wan_5b():
    assert vmr.DEFAULT_T2V_MODEL == "wan22-5b"
    assert vmr.DEFAULT_I2V_MODEL == "wan22-5b"


def test_preflight_ltx25_requires_install(monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda _m: False)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: True)
    ok, err = vmr.preflight_video_model("ltx25-distilled-int8")
    assert ok is False
    assert "not installed" in err.lower()


def test_preflight_ltx25_requires_companion(monkeypatch):
    def _installed(mid):
        return mid == "ltx25-distilled-int8"

    monkeypatch.setattr(vmr, "is_model_installed", _installed)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: True)
    ok, err = vmr.preflight_video_model("ltx25-distilled-int8")
    assert ok is False
    assert "companion" in err.lower()


def test_preflight_ltx25_requires_comfy32(monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda _m: True)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: False)
    ok, err = vmr.preflight_video_model("ltx25-distilled-int8")
    assert ok is False
    assert "0.32" in err


def test_preflight_ltx23_comfy_message_unchanged(monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda _m: True)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: False)
    ok, err = vmr.preflight_video_model("ltx23-distilled-fp8")
    assert ok is False
    assert "2.3" in err
    assert "0.32" not in err


def test_preflight_ltx25_ok(monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda _m: True)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: True)
    ok, err = vmr.preflight_video_model("ltx25-distilled-int8")
    assert ok is True
    assert err == ""