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

    @pytest.mark.parametrize("message,is_retry", [
        ("try again", True),
        ("retry please", True),
        ("Try again.", True),
        ("how do I retry a failed HTTP request?", False),
        ("can you try again with a blue sky", False),
    ])
    def test_only_a_short_retry_line_is_a_retry(self, message, is_retry):
        from backend.services.unified_chat_engine import _is_image_retry_message
        assert _is_image_retry_message(message) is is_retry

    def test_a_question_about_retrying_does_not_re_render(self, monkeypatch):
        import backend.services.unified_chat_engine as uce

        sid = "retry-question"
        calls = []
        retry = self._retry_engine(monkeypatch, calls)
        try:
            uce._SESSION_PENDING_IMAGE_PROMPT[sid] = "a castle at dusk"
            result = retry._try_image_generate_retry(
                "how do I retry a failed HTTP request?", sid, {}, lambda *a: None, "req",
            )
            assert result is None
            assert calls == []
        finally:
            uce._SESSION_PENDING_IMAGE_PROMPT.pop(sid, None)


class TestVideoRequests:
    """A new clip starts only from a request for one, never from a question that mentions video."""

    @pytest.fixture
    def uce(self, monkeypatch):
        import backend.services.unified_chat_engine as uce
        monkeypatch.setattr(uce, "_media_requires_explicit_command", lambda: False)
        return uce

    @pytest.mark.parametrize("message", [
        "make a video of a fox running",
        "make a video of a cat surfing",
        "Can you make a short video of waves crashing?",
        "Generate a 10 second cinematic video of a dragon flying over mountains",
        "I love foxes. Make a video of one in the snow",
        "video of a cat surfing",
    ])
    def test_requests_start_a_video(self, uce, message):
        assert uce.user_wants_video_generation(message) is True
        assert uce.user_wants_image_generation(message) is False

    @pytest.mark.parametrize("message", [
        "How do I make a video in Premiere?",
        "Make a video in DaVinci Resolve of my trip",
        "What's the best software to make a video on Linux?",
        "Why does it take so long to generate a video?",
        "Is it possible to make a video longer than 10 seconds?",
        "I'm going to make dinner and then watch a video",
        "make a list of video ideas",
        "Can you make a video call to my mom?",
        "Can you produce a video script for my channel?",
    ])
    def test_questions_and_mentions_start_nothing(self, uce, message):
        assert uce.user_wants_video_generation(message) is False
        # Turned down for video, the message does not become a picture request either.
        assert uce.user_wants_image_generation(message) is False

    def test_a_picture_that_mentions_video_is_still_a_picture(self, uce):
        message = "make a picture of my video game character"
        assert uce.user_wants_video_generation(message) is False
        assert uce.user_wants_image_generation(message) is True


class TestSlashMediaCommands:
    """/imagine and /image make a picture; only /video makes a clip."""

    @pytest.mark.parametrize("message", ["/imagine a red fox", "/image a red fox"])
    def test_image_commands_never_start_a_video(self, monkeypatch, message):
        import backend.services.unified_chat_engine as uce
        monkeypatch.setattr(uce, "_media_requires_explicit_command", lambda: False)
        assert uce.user_wants_video_generation(message) is False
        assert uce.user_wants_image_generation(message) is True

        engine = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
        engine.registry = type("R", (), {"get_tool": lambda self, n: object()})()
        assert engine._try_video_generate_direct(message, "sess", lambda *a, **k: None, "req", {}) is None

    def test_video_command_is_a_video(self, monkeypatch):
        import backend.services.unified_chat_engine as uce
        monkeypatch.setattr(uce, "_media_requires_explicit_command", lambda: False)
        assert uce.user_wants_video_generation("/video a red fox") is True
        assert uce.user_wants_image_generation("/video a red fox") is False


class TestVideoFromPictureInFocus:
    """A video request that points back at the picture animates it; it is never an image edit."""

    @pytest.fixture
    def uce(self, monkeypatch):
        import backend.services.unified_chat_engine as uce
        monkeypatch.setattr(uce, "_media_requires_explicit_command", lambda: False)
        return uce

    def test_long_run_of_describing_words_is_still_a_request(self, uce):
        assert uce.user_wants_video_generation(
            "create a short cinematic slow motion aerial drone video of a waterfall") is True
        assert uce.user_wants_video_generation("make the function that saves my video faster") is False

    @pytest.mark.parametrize("message", ["/video make it rain", "make a video of it"])
    def test_video_request_is_not_an_image_edit(self, uce, message):
        assert uce.user_wants_image_edit(message, has_recent_image=True) is False

    def test_plain_edit_still_edits(self, uce):
        assert uce.user_wants_image_edit("make it brighter", has_recent_image=True) is True

    @pytest.mark.parametrize("message", [
        "how do I remove the background in GIMP?",
        "how can I make it brighter in Photoshop?",
    ])
    def test_how_to_question_is_not_an_edit(self, uce, message):
        assert uce.user_wants_image_edit(message, has_recent_image=True) is False

    def _engine(self, uce, calls):
        engine = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
        engine.registry = type("R", (), {"get_tool": lambda self, n: object()})()
        engine._image_data = None
        engine._run_direct_tool_execution = lambda tool, params, *a, **k: calls.append((tool, params)) or {}
        return engine

    def test_picture_in_focus_is_the_first_frame(self, uce, tmp_path):
        sid = "sess-video-focus"
        picture = tmp_path / "last.png"
        picture.write_bytes(b"png")
        calls = []
        engine = self._engine(uce, calls)
        try:
            uce._remember_session_image(sid, str(picture))
            engine._try_video_generate_direct("/video make it rain", sid, lambda *a, **k: None, "req", {})
            engine._try_video_generate_direct("/video a fox in the snow", sid, lambda *a, **k: None, "req", {})
            uce._SESSION_IMAGE_FOCUS.discard(sid)
            engine._try_video_generate_direct("/video make it rain", sid, lambda *a, **k: None, "req", {})
        finally:
            uce._SESSION_LAST_EDIT.pop(sid, None)
            uce._SESSION_IMAGE_FOCUS.discard(sid)
        assert calls[0] == ("generate_video", {"prompt": "make it rain", "first_image": str(picture)})
        assert "first_image" not in calls[1][1], "a request with its own scene gets no first frame"
        assert "first_image" not in calls[2][1], "a picture out of focus is not animated"


class TestNamedPhotoToolsSkipHowTo:
    """Background removal, outpaint and identity run with no model in the loop."""

    @pytest.mark.parametrize("gate,message", [
        ("user_wants_background_remove", "how do I remove the background in GIMP?"),
        ("user_wants_outpaint", "how to outpaint in Krita"),
        ("user_wants_identity_generate", "how can I put this person in a different scene?"),
    ])
    def test_how_to_question_is_not_a_request(self, gate, message):
        import backend.services.unified_chat_engine as uce
        assert getattr(uce, gate)(message) is False

    @pytest.mark.parametrize("gate,message", [
        ("user_wants_background_remove", "remove the background"),
        ("user_wants_outpaint", "extend the canvas to the left"),
        ("user_wants_identity_generate", "put this person in a rainy alley"),
    ])
    def test_request_still_runs(self, gate, message):
        import backend.services.unified_chat_engine as uce
        assert getattr(uce, gate)(message) is True


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


_DECLINED = [
    "No, do not generate an image. Write the words.",
    "write me a prompt for Z Image Turbo, don't make an image",
    "no image please, just the text",
    "Don't generate an image yet, first write the prompt",
    "I want the words, not an image",
    "write the prompt without generating an image",
]
_WRITING_FOR_AN_IMAGE_MODEL = (
    "Please write 300 words or more describing (for Z Image Turbo) Jesus walking through "
    "an Old West town but with some Quentin Tarantino camera angle styles and shots. "
    "Wearing traditional Jesus robes. Angles should be dynamic, like worm's eye view "
    "(low angle), and dramatic lighting and such. Try one version now and I will test it."
)


class TestDeclinedOrWrittenMediaIsNotRendered:
    """"Do not generate an image" started a render: the direct path matched
    "generate an image" and never looked at the "do not". A request to write a
    prompt for an image model was rendered by the chat model, which the
    semantic selector had offered generate_image."""

    @pytest.mark.parametrize("message", _DECLINED)
    def test_a_declined_image_is_not_generated(self, message):
        from backend.services.unified_chat_engine import user_wants_image_generation, _wants_image_tools
        assert user_wants_image_generation(message) is False
        assert _wants_image_tools(message) is False

    @pytest.mark.parametrize("message", _DECLINED)
    def test_a_declined_image_is_not_pinned(self, message):
        from backend.services.unified_chat_engine import _pin_image_generation_tools
        selected = _pin_image_generation_tools(message, [], ["web_search", "generate_image"])
        assert "generate_image" not in selected

    def test_direct_path_stands_down_for_a_decline(self):

        from backend.services.unified_chat_engine import UnifiedChatEngine

        engine = UnifiedChatEngine.__new__(UnifiedChatEngine)
        engine.registry = type("R", (), {"get_tool": lambda self, n: object()})()
        result = engine._try_image_generate_direct(
            "No, do not generate an image. Write the words.",
            "sess", lambda *a, **k: None, "req", {},
        )
        assert result is None

    @pytest.mark.parametrize("message", _DECLINED + [
        _WRITING_FOR_AN_IMAGE_MODEL,
        "describe a sunset over Tucson",
        "can you write an image prompt for flux",
    ])
    def test_media_tools_are_withheld(self, message):
        from backend.services.unified_chat_engine import _withholds_media_tools
        assert _withholds_media_tools(message) is True

    @pytest.mark.parametrize("message", [
        "generate an image of a cat",
        "draw me a chicken",
        "generate a cat but don't make the picture too dark",
        "draw a cat, don't use any tools",
        "make an image of a gallery wall with no pictures",
        "write a prompt for Z-Image and render it",
        "write a prompt for a lighthouse at dusk and generate an image of it",
        "/imagine a red fox, no people",
        "/video a fox, don't make it dark",
        "what is the weather",
    ])
    def test_media_tools_are_kept(self, message):
        from backend.services.unified_chat_engine import _withholds_media_tools
        assert _withholds_media_tools(message) is False

    @pytest.mark.parametrize("message", [
        "generate a cat but don't make the picture too dark",
        "draw me a dragon, don't make the image look cartoonish",
    ])
    def test_an_instruction_about_the_picture_still_renders(self, message):
        from backend.services.unified_chat_engine import user_wants_image_generation
        assert user_wants_image_generation(message) is True

    def test_slash_command_renders_whatever_follows(self):
        from backend.services.unified_chat_engine import user_wants_image_generation
        assert user_wants_image_generation("/imagine a sign that reads do not generate an image") is True

    def test_declined_video_and_music_video(self):
        from backend.services.unified_chat_engine import user_wants_video_generation
        from backend.tools.video_pipeline_tools import wants_music_video
        assert user_wants_video_generation("do not make a video of this, describe it") is False
        assert user_wants_video_generation("make a video of a fox running") is True
        assert wants_music_video("don't make a music video, just write the treatment") is False

    def test_screen_agent_honours_a_decline(self):
        from backend.services.agent_brain import is_pure_image_request
        assert is_pure_image_request("draw a cat") is True
        assert is_pure_image_request("don't draw a picture, describe a cat") is False


class TestImageExampleFollowsTheOffer:
    """The prompt's generate_image example made the model call a tool it was
    not offered, for a request to write a prompt for an image model."""

    def test_no_image_example_without_the_tool(self):
        from backend.services.chat_prompt_blocks import build_chat_tools_prompt_tail
        tail = build_chat_tools_prompt_tail("- web_search(query:string) - Search the web")
        assert "<tool>generate_image</tool>" not in tail
        assert "<tool>web_search</tool>" in tail

    def test_image_example_with_the_tool(self):
        from backend.services.chat_prompt_blocks import build_chat_tools_prompt_tail
        tail = build_chat_tools_prompt_tail("- generate_image(prompt:string) - Generate an image")
        assert "<tool>generate_image</tool>" in tail


_EARLIER_PROMPT = (
    "Close-up cinematic shot framed on a small girl looking up at Jesus' face, holding a lamb. "
    "Only the coarse linen robe is visible. Golden hour light on a dusty Old West street."
)
_REWRITE_REQUEST = (
    "Please rewrite this so he is not carrying firearms, nor wearing a leather belt.\n\n"
    "---\n\nA small girl with smudged cheeks holds a lamb on a dusty street."
)


class TestPictureFromEarlierInTheChat:
    """"generate that image please", sent after the chat wrote a prompt, was
    rendered with "generate that image" as its prompt. The request points at
    the prompt already in the conversation."""

    @pytest.fixture
    def uce(self, monkeypatch):
        import backend.services.unified_chat_engine as uce
        monkeypatch.setattr(uce, "_media_requires_explicit_command", lambda: False)
        return uce

    def _engine(self, uce, calls):
        engine = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
        engine.registry = type("R", (), {"get_tool": lambda self, n: object()})()
        engine._image_data = None
        engine._save_message = lambda *a, **k: None
        engine._load_history = lambda session_id, limit=None: []
        engine._run_direct_tool_execution = lambda tool, params, *a, **k: calls.append((tool, params)) or {}
        return engine

    @staticmethod
    def _history(*replies):
        turns = []
        for reply in replies:
            turns += [{"role": "user", "content": "..."}, {"role": "assistant", "content": reply}]
        return turns

    @pytest.mark.parametrize("message", [
        "generate that image please",
        "Generate that image.",
        "ok, generate that image now",
        "make an image from the prompt above",
        "create an image from that prompt",
        "render that picture again",
        "make a picture of that",
    ])
    def test_a_bare_reference_is_only_the_reference(self, uce, message):
        assert uce.earlier_picture_reference(message) == "only"

    @pytest.mark.parametrize("message", [
        "generate that image, but make the sky stormy",
        "generate that image with a red sky",
    ])
    def test_a_reference_with_a_change_is_changed(self, uce, message):
        assert uce.earlier_picture_reference(message) == "changed"

    @pytest.mark.parametrize("message", [
        "generate an image of a red fox",
        "draw me a picture of a cat",
        "generate an image of that dog in the snow",
    ])
    def test_a_request_with_its_own_subject_is_not_a_reference(self, uce, message):
        assert uce.earlier_picture_reference(message) is None

    def test_the_prompt_written_as_a_tool_call_is_rendered(self, uce):
        calls = []
        history = self._history(f'generate_image(prompt: "{_EARLIER_PROMPT}")',
                                "Sorry, that didn't work: Stopped by the user before the image finished.")
        self._engine(uce, calls)._try_image_generate_direct(
            "generate that image please", "sess-refer", lambda *a, **k: None, "req", {"history": history})
        assert calls == [("generate_image", {"prompt": _EARLIER_PROMPT})]

    def test_the_prompt_written_as_prose_is_rendered(self, uce):
        calls = []
        history = self._history(f"Here is the revised prompt:\n\n{_EARLIER_PROMPT}")
        self._engine(uce, calls)._try_image_generate_direct(
            "generate that image", "sess-refer", lambda *a, **k: None, "req", {"history": history})
        assert calls == [("generate_image", {"prompt": _EARLIER_PROMPT})]

    def test_the_stored_conversation_is_read_without_a_history_option(self, uce):
        calls = []
        engine = self._engine(uce, calls)
        engine._load_history = lambda session_id, limit=None: self._history(_EARLIER_PROMPT)
        engine._try_image_generate_direct(
            "generate that image please", "sess-refer", lambda *a, **k: None, "req", {})
        assert calls == [("generate_image", {"prompt": _EARLIER_PROMPT})]

    def test_with_nothing_earlier_it_asks_instead_of_rendering(self, uce):
        calls, events = [], []
        self._engine(uce, calls)._try_image_generate_direct(
            "generate that image please", "sess-refer", lambda n, p: events.append((n, p)), "req", {})
        assert calls == []
        assert "don't see a prompt" in [p for n, p in events if n == "chat:complete"][0]["response"]

    def test_a_change_goes_to_the_chat_model(self, uce):
        calls = []
        result = self._engine(uce, calls)._try_image_generate_direct(
            "generate that image, but make the sky stormy", "sess-refer", lambda *a, **k: None, "req",
            {"history": self._history(_EARLIER_PROMPT)})
        assert result is None and calls == []

    def test_the_picture_in_focus_is_rendered_again(self, uce, tmp_path):
        sid = "sess-refer-focus"
        picture = tmp_path / "last.png"
        picture.write_bytes(b"png")
        calls = []
        try:
            uce._remember_session_image(sid, str(picture))
            uce._SESSION_LAST_IMAGE_PROMPT[sid] = _EARLIER_PROMPT
            self._engine(uce, calls)._try_image_generate_direct(
                "generate that image again", sid, lambda *a, **k: None, "req",
                {"history": self._history("Here's the generated image.")})
        finally:
            uce._SESSION_LAST_EDIT.pop(sid, None)
            uce._SESSION_IMAGE_FOCUS.discard(sid)
            uce._SESSION_LAST_IMAGE_PROMPT.pop(sid, None)
        assert calls == [("generate_image", {"prompt": _EARLIER_PROMPT})]

    def test_a_request_with_its_own_subject_is_unchanged(self, uce):
        calls = []
        self._engine(uce, calls)._try_image_generate_direct(
            "generate an image of a red fox", "sess-refer", lambda *a, **k: None, "req",
            {"history": self._history(_EARLIER_PROMPT)})
        assert calls == [("generate_image", {"prompt": "a red fox"})]

    @pytest.mark.parametrize("reply, prompt", [
        ('generate_image(prompt: "a fox in the snow")', "a fox in the snow"),
        ('generate_image(prompt="a fox in the snow", width=1024)', "a fox in the snow"),
        ('`generate_image(prompt: "a fox "in" the snow")`', 'a fox "in" the snow'),
        ("a fox in the snow", None),
        ('You could call generate_image(prompt: "a fox") for that.', None),
    ])
    def test_a_tool_call_written_as_text(self, uce, reply, prompt):
        assert uce._media_call_as_text(reply) == prompt


class TestRewriteGetsTheText:
    """"Rewrite this so ..." over a pasted scene came back as a generate_image
    call: the model was offered the media tools and the request read like a
    scene. A rewrite is a request for text."""

    @pytest.mark.parametrize("message", [
        "Rewrite this so the camera is directly behind Jesus:\n\nThe camera follows him.",
        _REWRITE_REQUEST,
        "can you revise this prompt so it is shorter",
    ])
    def test_media_tools_are_withheld(self, message):
        from backend.services.unified_chat_engine import _withholds_media_tools
        from backend.utils.request_phrases import asks_for_rewrite
        assert _withholds_media_tools(message) is True
        assert asks_for_rewrite(message) is True

    def test_a_rewrite_that_asks_for_the_picture_keeps_them(self):
        from backend.services.unified_chat_engine import _withholds_media_tools
        assert _withholds_media_tools("rewrite this prompt and generate an image of it") is False

    def test_a_reply_written_as_a_tool_call_reads_as_its_prompt(self, monkeypatch):
        from backend.services.chat_prompt_blocks import REWRITE_THE_TEXT_NOTE
        from backend.tests.test_unified_chat_host_hooks import _complete, _engine, _run

        e = _engine(monkeypatch, reply=f'generate_image(prompt: "{_EARLIER_PROMPT}")')
        result, events = _run(e, _REWRITE_REQUEST, {})

        assert _complete(events)["response"] == _EARLIER_PROMPT
        assert ("assistant", _EARLIER_PROMPT) in e.calls["saved"]
        assert {"content": _EARLIER_PROMPT, "reset": True, "session_id": "sess-host"} in [
            p for n, p in events if n == "chat:token"]
        assert not [n for n, p in events if n == "chat:tool_call"]
        system, *_, user = e.calls["llm_messages"][0]
        assert REWRITE_THE_TEXT_NOTE in user["content"]
        assert "generate_image tool" not in system["content"]


class TestStoppedRender:
    def test_a_stopped_render_reads_as_stopped_not_as_a_failure(self, monkeypatch):
        from types import SimpleNamespace
        import backend.services.unified_chat_engine as uce
        from backend.tools.image_tools import _RENDER_STOPPED

        class Registry:
            def get_tool(self, name):
                return SimpleNamespace(name=name, requires_approval=False)

            def execute_tool(self, name, **params):
                return SimpleNamespace(success=False, output=None, error=_RENDER_STOPPED,
                                       metadata={"stopped": True})

        monkeypatch.setattr(uce, "inject_chat_image_model", lambda tool, params, options=None: params)
        monkeypatch.setattr(uce, "is_aborted", lambda sid: False)
        monkeypatch.setattr(uce, "_preapproved_tool_names", lambda sid: set())
        engine = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
        engine.registry = Registry()
        engine._image_data = None
        engine._save_message = lambda *a, **k: None
        events = []
        engine._run_direct_tool_execution(
            "generate_image", {"prompt": "a fox"}, "sess-stop", lambda n, p: events.append((n, p)),
            "req", "generate an image of a fox", {})
        assert [p for n, p in events if n == "chat:complete"][0]["response"] == _RENDER_STOPPED

