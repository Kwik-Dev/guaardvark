"""
Tests for image generation tool selection.
Prevents regression: explicit image gen requests MUST include generate_image;
descriptive mentions of images must NOT force generation.
"""
import pytest


class TestImageToolSelection:
    """Verify that image-related messages always get the generate_image tool."""

    def _get_selected_tools(self, message):
        """Helper: run tool selection for a message and return tool names."""
        from backend.services.unified_chat_engine import select_tools_for_context
        all_tools = [
            "web_search", "analyze_website", "generate_image", "generate_animation",
            "browse_files", "read_file", "write_file", "execute_code",
            "agent_screen_capture", "agent_mode_start", "media_play",
        ]
        return select_tools_for_context(message, all_tools)

    @pytest.mark.parametrize("message", [
        "generate an image of a cat",
        "draw me a chicken",
        "create an image of a sunset",
        "make a picture of a dog",
        "make an image of a mountain",
        "render image of space",
        "generate a gif of a bouncing ball",
    ])
    def test_explicit_image_requests_include_generate_image_tool(self, message):
        """Explicit create-intent messages must include generate_image."""
        tools = self._get_selected_tools(message)
        assert "generate_image" in tools, (
            f"generate_image NOT selected for: {message!r}. Got: {tools}"
        )

    @pytest.mark.parametrize("message", [
        "hello",
        "how are you",
        "what's the weather",
        "tell me a joke",
        "Hi, on the client website there is an image of a duck",
        "The system prompt mentions generating images but I want to discuss the copy",
        "What does the image on the homepage show?",
        "photo of a beach",
        "image of a car",
        "picture of a house",
    ])
    def test_descriptive_messages_do_not_force_generate_image(self, message):
        """Descriptive / reference messages must not pin generate_image."""
        tools = self._get_selected_tools(message)
        assert "generate_image" not in tools, (
            f"generate_image wrongly selected for: {message!r}. Got: {tools}"
        )

    def test_system_prompt_contains_image_gen_rule(self):
        """The system prompt must instruct when to use generate_image."""
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine._is_voice_message = False
        prompt = engine._build_system_prompt(
            "You are helpful.",
            "- generate_image(prompt:str) - Generate an image"
        )
        assert "generate_image" in prompt
        assert "explicitly" in prompt.lower() or "NEW image" in prompt

    def test_voice_mode_appends_voice_instruction(self):
        """When is_voice_message=True, voice instruction should be appended."""
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine._is_voice_message = True
        prompt = engine._build_system_prompt(
            "You are helpful.",
            "- generate_image(prompt:str) - Generate an image"
        )
        assert "VOICE MODE" in prompt
        assert "spoken" in prompt.lower()

    def test_brain_state_prompt_contains_image_gen_rule(self):
        """BrainState chat prompt must include the shared image generation rule."""
        from backend.services.brain_state import BrainState

        brain = BrainState.__new__(BrainState)
        brain.system_prompts = {"chat": "You are helpful.\n\n{MEMORY_BLOCK}{DESKTOP_STATE}"}
        brain.tool_registry = None
        brain._app = None

        prompt = brain.get_system_prompt(
            role="chat",
            tool_list="- generate_image(prompt:string) - Generate an image",
        )
        assert "generate_image" in prompt
        assert "<prompt>" in prompt
        assert "<param_name>value</param_name>" not in prompt

    def test_pin_image_generation_on_retry_with_pending_session(self):
        from backend.services.unified_chat_engine import (
            _pin_image_generation_tools,
            _SESSION_PENDING_IMAGE_PROMPT,
        )

        sid = "pin-test"
        _SESSION_PENDING_IMAGE_PROMPT[sid] = "a castle"
        all_tools = ["web_search", "generate_image"]
        selected = _pin_image_generation_tools(
            "try again please", [], all_tools, session_id=sid,
        )
        assert "generate_image" in selected
        _SESSION_PENDING_IMAGE_PROMPT.pop(sid, None)

    def test_pin_image_generation_not_on_duck_website_message(self):
        from backend.services.unified_chat_engine import _pin_image_generation_tools

        all_tools = ["web_search", "generate_image"]
        selected = _pin_image_generation_tools(
            "Hi, on the client website there is an image of a duck",
            [],
            all_tools,
        )
        assert "generate_image" not in selected

    def test_try_again_not_agent_control_keyword(self):
        from backend.services.unified_chat_engine import TOOL_CONTEXT_KEYWORDS

        keywords = TOOL_CONTEXT_KEYWORDS["agent_control"][0]
        assert "try again" not in keywords


class TestUserWantsImageGeneration:
    @pytest.mark.parametrize("message", [
        "generate an image of a duck",
        "draw me a duck",
        "make a picture of a sunset",
    ])
    def test_explicit_requests(self, message):
        from backend.services.unified_chat_engine import user_wants_image_generation
        assert user_wants_image_generation(message) is True

    @pytest.mark.parametrize("message", [
        "Hi, on the client website there is an image of a duck",
        "What does the image on the homepage show?",
        "The system prompt mentions generating images but I want to discuss the copy",
    ])
    def test_descriptive_rejected(self, message):
        from backend.services.unified_chat_engine import user_wants_image_generation
        assert user_wants_image_generation(message) is False

    def test_try_image_generate_direct_skips_duck_website(self):
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine.registry = type("R", (), {"get_tool": lambda self, n: object()})()
        result = engine._try_image_generate_direct(
            "Hi, on the client website there is an image of a duck",
            "sess", lambda *a, **k: None, "req", {},
        )
        assert result is None


class TestCastLoraDirectIntercept:
    """A cast-LoRA request (e.g. \"[starship_captain] ... trained LoRA\") must be routed
    directly to generate_image with subject_ids, bypassing an LLM that refuses to call
    the tool because it hallucinates that no LoRA exists."""

    def _engine(self):
        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine.registry = type("R", (), {"get_tool": lambda self, n: object()})()
        return engine

    def test_cast_lora_request_routes_directly_with_subject_ids(self):
        from unittest.mock import patch

        engine = self._engine()
        captured = {}

        def fake_run(tool, params, session_id, emit_fn, request_id, message, options):
            captured["params"] = params
            return {"done": True}

        with patch.object(engine, "_run_direct_tool_execution", side_effect=fake_run), \
             patch("backend.tools.image_tools._resolve_cast_from_prompt", return_value=[12]):
            result = engine._try_image_generate_direct(
                "character reference sheet of [starship_captain] using his trained LoRA",
                "sess", lambda *a, **k: None, "req", {},
            )
        assert result == {"done": True}
        assert captured["params"].get("subject_ids") == [12]

    def test_plain_cast_mention_without_image_intent_is_not_hijacked(self):
        from unittest.mock import patch

        engine = self._engine()
        with patch("backend.tools.image_tools._resolve_cast_from_prompt", return_value=[12]):
            result = engine._try_image_generate_direct(
                "tell me about Starship Captain", "sess", lambda *a, **k: None, "req", {},
            )
        assert result is None
