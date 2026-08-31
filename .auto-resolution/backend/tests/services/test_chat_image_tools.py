"""Chat image tools: edit backend picker, intercepts, consent, registration."""
from backend.tools.image_tools import EditImageTool, GenerateIdentityTool
from backend.services.unified_chat_engine import (
    GPU_HEAVY_TOOLS,
    _pin_image_edit_tools,
    identity_prompt_from_message,
    parse_outpaint_pad,
    user_wants_background_remove,
    user_wants_identity_generate,
    user_wants_outpaint,
)


def test_pick_edit_backend_auto_prefers_qwen(monkeypatch):
    class FakeGen:
        def qwen_edit_installed(self):
            return True

        def _kontext_installed(self):
            return True

    monkeypatch.setattr(
        "backend.services.comfyui_image_generator.ComfyUIImageGenerator",
        lambda: FakeGen(),
    )
    assert EditImageTool._pick_edit_backend("auto") == "qwen"
    assert EditImageTool._pick_edit_backend("kontext") == "kontext"
    assert EditImageTool._pick_edit_backend("qwen-image-edit") == "qwen"
    assert EditImageTool._pick_edit_backend("zimage-turbo") == "img2img"


def test_pick_edit_backend_auto_falls_to_kontext(monkeypatch):
    class FakeGen:
        def qwen_edit_installed(self):
            return False

        def _kontext_installed(self):
            return True

    monkeypatch.setattr(
        "backend.services.comfyui_image_generator.ComfyUIImageGenerator",
        lambda: FakeGen(),
    )
    assert EditImageTool._pick_edit_backend("auto") == "kontext"


def test_identity_intent_does_not_steal_hat_on_person():
    assert user_wants_identity_generate("this person as a 1940s detective") is True
    assert user_wants_identity_generate("put this person in a rainy alley") is True
    assert user_wants_identity_generate("put a cowboy hat on this person") is False
    assert user_wants_identity_generate("same face, new scene") is True


def test_background_and_outpaint_intent():
    assert user_wants_background_remove("remove the background") is True
    assert user_wants_background_remove("transparent background please") is True
    assert user_wants_background_remove("remove the coffee cup") is False
    assert user_wants_outpaint("extend the canvas to the left") is True
    assert user_wants_outpaint("outpaint this photo") is True
    assert user_wants_outpaint("put a hat on him") is False


def test_parse_outpaint_pad_named_side():
    pad = parse_outpaint_pad("expand left")
    assert pad["left"] == 256
    assert pad["right"] == 0
    all_sides = parse_outpaint_pad("outpaint this")
    assert all_sides["left"] == all_sides["right"] == 256


def test_identity_prompt_strips_chrome():
    assert identity_prompt_from_message("this person as a 1940s detective") == "a 1940s detective"


def test_generate_identity_requires_consent():
    result = GenerateIdentityTool().execute(prompt="a detective", consented=False)
    assert result.success is False
    assert "consented" in (result.error or "").lower()
    result_str = GenerateIdentityTool().execute(prompt="a detective", consented="false")
    assert result_str.success is False


def test_pin_image_edit_tools_when_attached():
    selected = _pin_image_edit_tools(
        True, ["web_search"],
        ["edit_image", "remove_background", "inpaint_image", "outpaint_image",
         "generate_identity", "web_search"],
    )
    assert selected[0] == "edit_image"
    assert "remove_background" in selected
    assert "generate_identity" in selected
    unchanged = _pin_image_edit_tools(False, ["web_search"], ["edit_image", "web_search"])
    assert unchanged == ["web_search"]


def test_gpu_heavy_includes_identity_not_rembg():
    assert "generate_identity" in GPU_HEAVY_TOOLS
    assert "inpaint_image" in GPU_HEAVY_TOOLS
    assert "outpaint_image" in GPU_HEAVY_TOOLS
    assert "remove_background" not in GPU_HEAVY_TOOLS


def test_named_image_tools_register(monkeypatch):
    from backend.tools.tool_registry_init import register_image_tools
    monkeypatch.delenv("GUAARDVARK_IDENTITY_TOOL", raising=False)
    names = register_image_tools()
    for n in ("edit_image", "remove_background", "inpaint_image", "outpaint_image"):
        assert n in names
    assert "generate_identity" not in names  # off until its likeness is verified
    monkeypatch.setenv("GUAARDVARK_IDENTITY_TOOL", "1")
    assert "generate_identity" in register_image_tools()


def test_named_image_direct_identity_not_edit():
    from backend.services.unified_chat_engine import UnifiedChatEngine

    class FakeRegistry:
        def get_tool(self, name):
            return object() if name in (
                "generate_identity", "remove_background", "outpaint_image", "edit_image",
            ) else None

    engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
    engine.registry = FakeRegistry()
    engine._save_message = lambda *a, **k: None
    engine._image_data = None
    engine._calls = []

    def _run(tool, params, *a, **k):
        engine._calls.append((tool, params))
        return {"success": True, "tool": tool}

    engine._run_direct_tool_execution = _run
    engine._chat_image_source = lambda sid: "/tmp/face.png"

    result = engine._try_named_image_direct(
        "this person as a 1940s detective", "s", lambda *a: None, "r", {},
    )
    assert result is not None
    assert engine._calls[0][0] == "generate_identity"
    assert engine._calls[0][1]["consented"] is True

    engine._calls.clear()
    skipped = engine._try_named_image_direct(
        "put a cowboy hat on this person", "s", lambda *a: None, "r", {},
    )
    assert skipped is None
