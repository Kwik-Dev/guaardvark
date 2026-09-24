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

# ---- prepare_video_model: start ComfyUI when that is all that is missing ----
@pytest.fixture
def bridge(monkeypatch):
    from backend.services import plugin_bridge

    calls = []
    state = {"up": False}
    monkeypatch.setattr(plugin_bridge, "auto_orchestrator_enabled", lambda: True)

    def ensure(context, stage, **kw):
        calls.append((context, stage))
        state["up"] = True

    monkeypatch.setattr(plugin_bridge, "ensure_plugins_for_stage", ensure)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: state["up"])
    monkeypatch.setattr(vmr, "is_model_installed", lambda _m: True)
    return plugin_bridge, calls, state


def test_prepare_starts_comfyui_and_passes(bridge):
    _, calls, _ = bridge
    ok, err = vmr.prepare_video_model("wan22-14b")
    assert ok is True and err == ""
    assert calls == [("video", "generating")]


def test_prepare_does_nothing_when_ready(bridge):
    _, calls, state = bridge
    state["up"] = True
    assert vmr.prepare_video_model("wan22-14b") == (True, "")
    assert calls == []


def test_prepare_never_starts_for_a_missing_model(bridge, monkeypatch):
    _, calls, _ = bridge
    monkeypatch.setattr(vmr, "is_model_installed", lambda _m: False)
    ok, err = vmr.prepare_video_model("wan22-14b")
    assert ok is False and "not installed" in err.lower()
    assert calls == []


def test_prepare_respects_the_orchestrator_switch(bridge, monkeypatch):
    plugin_bridge, calls, _ = bridge
    monkeypatch.setattr(plugin_bridge, "auto_orchestrator_enabled", lambda: False)
    ok, err = vmr.prepare_video_model("wan22-14b")
    assert ok is False and "Start the ComfyUI plugin" in err
    assert calls == []


def test_prepare_reports_a_refused_start(bridge, monkeypatch):
    plugin_bridge, _, _ = bridge

    def refuse(context, stage, **kw):
        raise plugin_bridge.PluginUnavailable("plugin 'comfyui' is user-disabled")

    monkeypatch.setattr(plugin_bridge, "ensure_plugins_for_stage", refuse)
    ok, err = vmr.prepare_video_model("wan22-14b")
    assert ok is False and "user-disabled" in err
