#!/usr/bin/env python3
"""Tests for tier routing — escalation, deliberation heuristic, vision detection."""

import pytest

from backend.services.agent_brain import (
    AgentBrain,
    SOCIAL_TIER2_PATTERNS,
    DELIBERATION_SIGNALS,
    VISION_PATTERNS,
    TEXT_ANALYSIS_PATTERNS,
)
from backend.services.brain_state import BrainState, _build_default_reflexes


@pytest.fixture(autouse=True)
def reset_singleton():
    BrainState.reset()
    yield
    BrainState.reset()


@pytest.fixture
def brain():
    state = BrainState.get_instance()
    state.reflexes = _build_default_reflexes(tool_registry=None)
    state.health.reflexes_loaded = True
    state.health.llm_available = False  # prevent actual LLM calls
    state.health.tools_available = False
    return AgentBrain(state=state)


# ---------------------------------------------------------------------------
# Deliberation heuristic
# ---------------------------------------------------------------------------

class TestNeedsDeliberation:
    def test_multi_step_detected(self, brain):
        assert brain._needs_deliberation(
            "First research the topic, then write a report"
        )

    def test_research_and_create(self, brain):
        assert brain._needs_deliberation(
            "Research quantum computing and create a summary"
        )

    def test_analyze_and_improve(self, brain):
        assert brain._needs_deliberation(
            "Analyze the code and then improve its performance"
        )

    def test_compare_and_recommend(self, brain):
        assert brain._needs_deliberation(
            "Compare these two approaches and then recommend the best one"
        )

    def test_help_figure_out(self, brain):
        assert brain._needs_deliberation(
            "Help me figure out the best approach for this"
        )

    def test_simple_question_not_deliberation(self, brain):
        assert not brain._needs_deliberation("What is the weather today?")

    def test_simple_command_not_deliberation(self, brain):
        assert not brain._needs_deliberation("Analyze this website")

    def test_empty_not_deliberation(self, brain):
        assert not brain._needs_deliberation("")

    def test_greeting_not_deliberation(self, brain):
        assert not brain._needs_deliberation("hello")


# ---------------------------------------------------------------------------
# Vision detection
# ---------------------------------------------------------------------------

class TestVisionDetection:
    def test_virtual_screen(self, brain):
        assert brain._is_vision_task("Check the virtual screen")

    def test_agent_screen(self, brain):
        assert brain._is_vision_task("Use the agent screen to navigate")

    def test_your_screen(self, brain):
        assert brain._is_vision_task("What's on your screen?")

    def test_slash_vision(self, brain):
        assert brain._is_vision_task("/vision take a screenshot")

    def test_slash_agent(self, brain):
        assert brain._is_vision_task("/agent open youtube")

    def test_image_data_is_vision(self, brain):
        assert brain._is_vision_task("describe this", image_data="base64data")

    def test_normal_message_not_vision(self, brain):
        assert not brain._is_vision_task("What is the capital of France?")

    def test_website_analysis_not_vision(self, brain):
        assert not brain._is_vision_task("Analyze this website for SEO")


# ---------------------------------------------------------------------------
# Conversational passthrough
# ---------------------------------------------------------------------------

class TestSocialTier2Passthrough:
    def test_yes_is_social(self):
        assert SOCIAL_TIER2_PATTERNS.fullmatch("yes")

    def test_no_is_social(self):
        assert SOCIAL_TIER2_PATTERNS.fullmatch("no")

    def test_ok_is_social(self):
        assert SOCIAL_TIER2_PATTERNS.fullmatch("ok")

    def test_sure_is_social(self):
        assert SOCIAL_TIER2_PATTERNS.fullmatch("sure")

    def test_sounds_good_is_social(self):
        assert SOCIAL_TIER2_PATTERNS.fullmatch("sounds good")

    def test_complex_sentence_not_social(self):
        assert not SOCIAL_TIER2_PATTERNS.fullmatch(
            "Yes, please analyze the website"
        )

    def test_question_not_social(self):
        assert not SOCIAL_TIER2_PATTERNS.fullmatch(
            "What do you think about this approach?"
        )


# ---------------------------------------------------------------------------
# Tier routing integration
# ---------------------------------------------------------------------------

class TestTierRouting:
    def test_greeting_routes_to_tier2(self, brain):
        """Social openers use Tier 2 LLM (skip_tools), not hardcoded reflexes."""
        result = brain.process(
            session_id="test",
            message="hello",
            options={},
            emit_fn=lambda e, d: None,
        )
        assert result.get("tier") == 2
        assert result.get("success") is False  # no LLM in test fixture
        assert "Model not loaded" in (result.get("error") or "")

    def test_farewell_routes_to_tier2(self, brain):
        result = brain.process(
            session_id="test",
            message="goodbye",
            options={},
            emit_fn=lambda e, d: None,
        )
        assert result.get("tier") == 2

    def test_thanks_routes_to_tier2(self, brain):
        result = brain.process(
            session_id="test",
            message="thanks!",
            options={},
            emit_fn=lambda e, d: None,
        )
        assert result.get("tier") == 2

    def test_complex_message_routes_to_tier2(self, brain):
        """Non-greeting, non-deliberation message should go to Tier 2."""
        # Since LLM is unavailable, Tier 2 will return an error
        result = brain.process(
            session_id="test",
            message="What is the capital of France?",
            options={},
            emit_fn=lambda e, d: None,
        )
        # Should attempt Tier 2 (which fails due to no LLM)
        assert result.get("tier") == 2

    def test_multi_step_routes_to_tier3(self, brain):
        """Multi-step request should route to Tier 3."""
        result = brain.process(
            session_id="test",
            message="Research quantum computing and then write a summary",
            options={},
            emit_fn=lambda e, d: None,
        )
        # Tier 3 falls back to Tier 2 (no tools), which fails (no LLM)
        assert result.get("tier") in (2, 3)

    def test_force_tier3(self, brain):
        """force_tier=3 should skip reflexes and go to Tier 3."""
        result = brain.process(
            session_id="test",
            message="hello",  # would normally be a reflex
            options={},
            emit_fn=lambda e, d: None,
            force_tier=3,
        )
        # Tier 3 degrades because no tools and no LLM
        assert result.get("tier") in (2, 3)

    def test_conversational_passthrough_to_tier2(self, brain):
        """Bare 'yes' should go to Tier 2 social path."""
        result = brain.process(
            session_id="test",
            message="yes",
            options={},
            emit_fn=lambda e, d: None,
        )
        assert result.get("tier") == 2

    def test_social_without_llm_returns_honest_error(self, brain):
        result = brain.process(
            session_id="test",
            message="hello",
            options={},
            emit_fn=lambda e, d: None,
        )
        assert result.get("success") is False
        assert "Model not loaded" in (result.get("error") or "")
        assert "Hey!" not in (result.get("response") or "")

    def test_emit_events_on_tier2_social(self, brain):
        """Social path attempts Tier 2 (emits error when LLM unavailable)."""
        events = []
        result = brain.process(
            session_id="test",
            message="hello",
            options={},
            emit_fn=lambda e, d: events.append(e),
        )
        assert result.get("tier") == 2
        assert result.get("success") is False


# ---------------------------------------------------------------------------
# Text-extraction / analysis over a pasted prompt (must NOT trigger image gen)
# ---------------------------------------------------------------------------

class TestTextAnalysisRouting:
    """'extract/describe/summarize/analyze a prompt' is a TEXT task — it must
    route to Tier 2 skip_tools so generate_image is never offered, even when the
    pasted text reads exactly like an image prompt."""

    def test_extract_character_description_matches(self):
        msg = (
            "extract the character description\n\n"
            "medium close-up of a weathered starship captain in a dark navy "
            "uniform with gold trim, facing the viewport, face lit by cold blue "
            "starfield glow and warm amber holographic readout reflecting off his "
            "cheek, sweat beads on his brow, eyes fixed on a distant planet, "
            "blurred bridge consoles with pulsing red alert lights, crew out of "
            "focus, dramatic chiaroscuro lighting, cinematic depth of field, "
            "photorealistic"
        )
        assert TEXT_ANALYSIS_PATTERNS.search(msg)

    def test_describe_scene_matches(self):
        assert TEXT_ANALYSIS_PATTERNS.search(
            "describe the scene in this prompt: a woman on a motorcycle at night"
        )

    def test_summarize_prompt_matches(self):
        assert TEXT_ANALYSIS_PATTERNS.search("summarize the prompt for me")

    def test_analyze_lighting_matches(self):
        assert TEXT_ANALYSIS_PATTERNS.search(
            "analyze the lighting and style of this description"
        )

    def test_legit_image_generation_does_not_match(self):
        for msg in (
            "generate an image of a woman on a motorcycle",
            "draw a cat",
            "create a picture of a sunset",
            "make an image of a starship captain",
            "visualize a futuristic city",
            "render a portrait of a king",
        ):
            assert not TEXT_ANALYSIS_PATTERNS.search(msg), msg

    def test_plain_chat_does_not_match(self):
        assert not TEXT_ANALYSIS_PATTERNS.search("hello there")

    def test_extract_routes_to_tier2_skip_tools(self, brain):
        """The reported bug: 'extract the character description' + image prompt
        must route to Tier 2 (skip_tools), never to image generation."""
        result = brain.process(
            session_id="test",
            message=(
                "extract the character description\n\n"
                "medium close-up of a weathered starship captain in a dark navy "
                "uniform with gold trim, dramatic chiaroscuro lighting, cinematic "
                "depth of field, photorealistic"
            ),
            options={},
            emit_fn=lambda e, d: None,
        )
        # Tier 2 skip_tools path (fails only because no LLM in the test fixture)
        assert result.get("tier") == 2
        assert result.get("success") is False
        assert "Model not loaded" in (result.get("error") or "")
