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


class TestPastedDescriptionsDoNotGenerate:
    """A pasted prompt or scene description is not a request to render it.

    These all reached generate_image through substring matching: "withdrawal"
    contains "draw", "animated reflections" contains "animate". The direct
    natural-language path bypasses the LLM, so a match here rendered an image
    with nothing to veto it.
    """

    @pytest.mark.parametrize("message", [
        "A cinematic wide shot of a rain-soaked alley, neon signs, "
        "animated reflections on wet asphalt",
        "Here's the prompt I want to save for later: lone astronaut on a red "
        "dune, drawn in ink wash",
        "Can you review this description? Slow push-in on a lighthouse, gulls "
        "wheeling, moving image quality",
        "The client asked for a withdrawal form redesign",
        "The animation industry uses a lot of GPU time",
    ])
    def test_pasted_description_does_not_want_generation(self, message):
        from backend.services.unified_chat_engine import user_wants_image_generation
        assert user_wants_image_generation(message) is False

    @pytest.mark.parametrize("message", [
        "The client asked for a withdrawal form redesign",
        "A cinematic wide shot with animated reflections on wet asphalt",
    ])
    def test_pasted_description_does_not_pin_generate_image(self, message):
        from backend.services.unified_chat_engine import _pin_image_generation_tools
        selected = _pin_image_generation_tools(message, [], ["web_search", "generate_image"])
        assert "generate_image" not in selected


# (message, no picture in the session, picture attached or just made, older picture)
_IMAGE_INTENT_ROWS = [
    ("Draw me a cat wearing a top hat", "new", "new", "new"),
    ("Can you draw a dragon over a castle at night?", "new", "new", "new"),
    ("generate an image of a lighthouse at dusk", "new", "new", "new"),
    ("animate this logo spinning slowly", "new", "new", "new"),
    ("make a picture of my dog as an astronaut", "new", "new", "new"),
    ("How do I animate a CSS button on hover?", "-", "-", "-"),
    ("Can you draw a conclusion from these numbers?", "-", "-", "-"),
    ("Can you fix the grammar in this sentence?", "-", "-", "-"),
    ("How do I make an image responsive in CSS?", "-", "-", "-"),
    ("Make sure the image path in config.yaml is correct", "-", "-", "-"),
    ("Please add error handling to this function", "-", "-", "-"),
    ("We should draw the line at 50 requests per minute", "-", "-", "-"),
    ("make it bigger", "-", "edit", "-"),
    ("add a red scarf to the horse", "-", "edit", "-"),
    ("change the date format to ISO 8601", "-", "-", "-"),
    ("now change the sky in the last image to sunset", "-", "edit", "edit"),
]


class TestImageIntent:
    """New picture, edit of the session's picture, or neither.

    The engine tries the edit first and new-image generation after it, and both
    run with no model in the loop, so a false hit starts a GPU job.
    """

    @staticmethod
    def _route(message, has_recent_image, has_stale_image):
        from backend.services.unified_chat_engine import (
            user_wants_image_edit,
            user_wants_image_generation,
        )
        if user_wants_image_edit(message, has_recent_image, has_stale_image):
            return "edit"
        return "new" if user_wants_image_generation(message) else "-"

    @pytest.mark.parametrize("message,no_image,recent,stale", _IMAGE_INTENT_ROWS)
    def test_route(self, message, no_image, recent, stale):
        assert self._route(message, False, False) == no_image
        assert self._route(message, True, False) == recent
        assert self._route(message, False, True) == stale

    def test_mid_sentence_draw_is_left_to_the_chat_model(self):
        from backend.services.unified_chat_engine import (
            _pin_image_generation_tools,
            user_wants_image_generation,
        )
        message = "I'd like you to draw a cat"
        assert user_wants_image_generation(message) is False
        selected = _pin_image_generation_tools(message, [], ["web_search", "generate_image"])
        assert "generate_image" in selected


class TestImageFocus:
    """A follow-up edit applies right after an image turn, or when it names the image."""

    def test_plain_chat_turn_ends_the_follow_up_edit(self, monkeypatch, tmp_path):
        import backend.services.media_director as media_director
        import backend.services.unified_chat_engine as uce
        from backend.tests.test_unified_chat_host_hooks import (
            _engine as chat_engine,
            _run as chat_turn,
        )

        sid = "sess-host"  # the session chat_turn runs in
        picture = tmp_path / "last.png"
        picture.write_bytes(b"png")
        edits = []

        class Registry:
            def get_tool(self, name):
                return object() if name == "edit_image" else None

            def execute_tool(self, tool_name, **params):
                edits.append(params["instruction"])
                raise RuntimeError("no render in a unit test")

        monkeypatch.setattr(media_director, "refine_edit_instruction", lambda text, **kw: text)
        editor = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
        editor.registry = Registry()
        editor._image_data = None
        editor._save_message = lambda *a, **k: None

        def follow_up(message):
            editor._try_image_edit_direct(message, sid, lambda *a: None, "req", {})

        try:
            uce._remember_session_image(sid, str(picture))
            follow_up("make it bigger")
            assert edits == ["make it bigger"]

            chat_turn(chat_engine(monkeypatch), "hello there, how are you today", {})
            assert sid not in uce._SESSION_IMAGE_FOCUS

            follow_up("make it bigger")
            assert edits == ["make it bigger"]
            follow_up("make the last image bigger")
            assert edits == ["make it bigger", "make the last image bigger"]
        finally:
            uce._SESSION_LAST_EDIT.pop(sid, None)
            uce._SESSION_IMAGE_FOCUS.discard(sid)


class TestImageRetry:
    """A "try again" after a failed render applies to the next turn only."""

    @staticmethod
    def _retry_engine(monkeypatch, calls):
        import backend.services.unified_chat_engine as uce

        class Registry:
            def get_tool(self, name):
                return object() if name in ("generate_image", "edit_image") else None

        monkeypatch.setattr(uce, "resolve_chat_image_model", lambda *a, **k: "test-model")
        monkeypatch.setattr(uce, "inject_chat_image_model", lambda tool, params, options=None: params)
        engine = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
        engine.registry = Registry()
        engine._run_direct_tool_execution = (
            lambda tool, params, *a, **k: calls.append((tool, params)) or {"success": True}
        )
        return engine

    def test_pending_retry_expires_after_a_chat_turn(self, monkeypatch, tmp_path):
        import backend.services.unified_chat_engine as uce
        from backend.tests.test_unified_chat_host_hooks import (
            _engine as chat_engine,
            _run as chat_turn,
        )

        sid = "sess-host"  # the session chat_turn runs in
        picture = tmp_path / "last.png"
        picture.write_bytes(b"png")
        calls = []
        retry = self._retry_engine(monkeypatch, calls)

        def try_again():
            for attempt in (retry._try_image_generate_retry, retry._try_image_edit_retry):
                attempt("try again", sid, {}, lambda *a: None, "req")

        try:
            uce._SESSION_PENDING_IMAGE_PROMPT[sid] = "a castle at dusk"
            uce._SESSION_PENDING_IMAGE_EDIT[sid] = {"instruction": "add a moat", "image": str(picture)}
            try_again()
            assert [tool for tool, _ in calls] == ["generate_image", "edit_image"]

            chat_turn(chat_engine(monkeypatch), "hello there, how are you today", {})
            assert sid not in uce._SESSION_PENDING_IMAGE_PROMPT
            assert sid not in uce._SESSION_PENDING_IMAGE_EDIT

            calls.clear()
            try_again()
            assert calls == []
        finally:
            uce._SESSION_PENDING_IMAGE_PROMPT.pop(sid, None)
            uce._SESSION_PENDING_IMAGE_EDIT.pop(sid, None)


class TestCommandOnlyMode:
    """chat_media_requires_command: only an explicit command may create media."""

    @staticmethod
    def _force(monkeypatch, enabled):
        import backend.services.unified_chat_engine as uce
        monkeypatch.setattr(uce, "_media_requires_explicit_command", lambda: enabled)
        return uce

    @pytest.mark.parametrize("message", [
        "generate an image of a cat",
        "draw me a duck",
        "make a picture of a sunset",
    ])
    def test_natural_language_suppressed_when_on(self, monkeypatch, message):
        uce = self._force(monkeypatch, True)
        assert uce.user_wants_image_generation(message) is False

    @pytest.mark.parametrize("message", [
        "/imagine a fox in tall grass",
        "  /imagine a fox",
    ])
    def test_slash_command_still_honoured_when_on(self, monkeypatch, message):
        uce = self._force(monkeypatch, True)
        assert uce.user_wants_image_generation(message) is True

    def test_natural_language_works_when_off(self, monkeypatch):
        uce = self._force(monkeypatch, False)
        assert uce.user_wants_image_generation("generate an image of a cat") is True

    def test_video_suppressed_when_on_but_slash_survives(self, monkeypatch):
        uce = self._force(monkeypatch, True)
        assert uce.user_wants_video_generation("generate a video of a fox") is False
        assert uce.user_wants_video_generation("/video a fox") is True

    def test_defaults_off_so_existing_behaviour_is_unchanged(self):
        from backend.services.unified_chat_engine import _media_requires_explicit_command
        assert _media_requires_explicit_command() is False

    def test_video_chrome_strips_slash_and_nl(self):
        from backend.services.unified_chat_engine import _VIDEO_CHROME_RE
        assert _VIDEO_CHROME_RE.sub("", "/video a red cube").strip() == "a red cube"
        assert _VIDEO_CHROME_RE.sub("", "generate a video of a red cube").strip() == "a red cube"

    def test_gpu_heavy_tools_includes_generate_video(self):
        from backend.services.unified_chat_engine import GPU_HEAVY_TOOLS
        assert "generate_video" in GPU_HEAVY_TOOLS
        assert "generate_image" in GPU_HEAVY_TOOLS
        assert "generate_identity" in GPU_HEAVY_TOOLS
        assert "remove_background" not in GPU_HEAVY_TOOLS
        assert "generate_music_video" not in GPU_HEAVY_TOOLS
        assert "start_film_crew" not in GPU_HEAVY_TOOLS
