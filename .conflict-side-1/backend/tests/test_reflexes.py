#!/usr/bin/env python3
"""Tests for Tier 1 reflex system — matching, context-free rule, verification, fallback."""

import re
from unittest.mock import MagicMock

import pytest

from backend.services.brain_state import (
    ReflexAction,
    ReflexResult,
    _build_default_reflexes,
)


@pytest.fixture
def mock_registry():
    """Registry where all media tools exist and succeed."""
    registry = MagicMock()
    registry.get_tool.return_value = MagicMock()
    registry.execute_tool.return_value = MagicMock(
        success=True,
        output={"status": "ok", "title": "Song Name"},
    )
    return registry


@pytest.fixture
def mock_registry_failing():
    """Registry where tools exist but execution fails."""
    registry = MagicMock()
    registry.get_tool.return_value = MagicMock()
    registry.execute_tool.return_value = MagicMock(
        success=False,
        output=None,
        error="Tool failed",
    )
    return registry


# ---------------------------------------------------------------------------
# Context-free rule enforcement
# ---------------------------------------------------------------------------

class TestContextFreeRule:
    """Reflexes must only match messages unambiguous without conversation history."""

    def test_bare_yes_not_a_reflex(self):
        reflexes = _build_default_reflexes(tool_registry=None)
        for r in reflexes:
            for p in r.patterns:
                assert not p.fullmatch("yes"), (
                    f"Reflex '{r.name}' matches bare 'yes'"
                )

    def test_bare_ok_not_a_reflex(self):
        reflexes = _build_default_reflexes(tool_registry=None)
        for r in reflexes:
            for p in r.patterns:
                assert not p.fullmatch("ok"), (
                    f"Reflex '{r.name}' matches bare 'ok'"
                )

    def test_bare_no_not_a_reflex(self):
        reflexes = _build_default_reflexes(tool_registry=None)
        for r in reflexes:
            for p in r.patterns:
                assert not p.fullmatch("no"), (
                    f"Reflex '{r.name}' matches bare 'no'"
                )

    def test_sure_not_a_reflex(self):
        reflexes = _build_default_reflexes(tool_registry=None)
        for r in reflexes:
            for p in r.patterns:
                assert not p.fullmatch("sure"), (
                    f"Reflex '{r.name}' matches bare 'sure'"
                )

    def test_no_social_reflexes_in_table(self):
        reflexes = _build_default_reflexes(tool_registry=None)
        names = {r.name for r in reflexes}
        assert "greeting" not in names
        assert "farewell" not in names
        assert "thanks" not in names


# ---------------------------------------------------------------------------
# Media reflex matching
# ---------------------------------------------------------------------------

class TestMediaReflexMatching:
    def test_play_matches(self, mock_registry):
        reflexes = _build_default_reflexes(tool_registry=mock_registry)
        play = next(r for r in reflexes if r.name == "media_play")
        for p in play.patterns:
            assert p.fullmatch("play some jazz")
            assert p.fullmatch("Play my favorite song")

    def test_control_matches(self, mock_registry):
        reflexes = _build_default_reflexes(tool_registry=mock_registry)
        control = next(r for r in reflexes if r.name == "media_control")
        for p in control.patterns:
            assert p.fullmatch("pause")
            assert p.fullmatch("stop the music")
            assert p.fullmatch("next track")
            assert p.fullmatch("skip")
            assert p.fullmatch("resume")

    def test_volume_matches(self, mock_registry):
        reflexes = _build_default_reflexes(tool_registry=mock_registry)
        vol = next(r for r in reflexes if r.name == "media_volume")
        for msg in ("volume up", "volume 50", "louder", "mute"):
            assert any(p.fullmatch(msg) for p in vol.patterns), msg
        assert not any(
            p.fullmatch("please volume up the analysis")
            for p in vol.patterns
        )

    def test_status_matches(self, mock_registry):
        reflexes = _build_default_reflexes(tool_registry=mock_registry)
        status = next(r for r in reflexes if r.name == "media_status")
        for msg in ("what's playing", "current song"):
            assert any(p.fullmatch(msg) for p in status.patterns), msg

    def test_play_does_not_match_non_media(self, mock_registry):
        reflexes = _build_default_reflexes(tool_registry=mock_registry)
        play = next(r for r in reflexes if r.name == "media_play")
        # "play" alone without content should still match (it's a command)
        # but complex non-media sentences should not trigger control reflexes
        control = next(r for r in reflexes if r.name == "media_control")
        for p in control.patterns:
            assert not p.fullmatch("can you please stop the analysis and start over")


# ---------------------------------------------------------------------------
# Media reflex execution
# ---------------------------------------------------------------------------

class TestMediaReflexExecution:
    def test_play_calls_tool(self, mock_registry):
        reflexes = _build_default_reflexes(tool_registry=mock_registry)
        play = next(r for r in reflexes if r.name == "media_play")
        match = play.patterns[0].fullmatch("play some jazz")
        result = play.handler("play some jazz", match, {})

        assert result.success is True
        assert result.tool_called == "media_play"
        mock_registry.execute_tool.assert_called_once()
        call_kwargs = mock_registry.execute_tool.call_args
        assert call_kwargs[1].get("query") == "some jazz"

    def test_control_extracts_action(self, mock_registry):
        reflexes = _build_default_reflexes(tool_registry=mock_registry)
        control = next(r for r in reflexes if r.name == "media_control")
        match = control.patterns[0].fullmatch("pause")
        result = control.handler("pause", match, {})

        assert result.success is True
        call_kwargs = mock_registry.execute_tool.call_args
        assert call_kwargs[1].get("action") == "pause"

    def test_skip_maps_to_next(self, mock_registry):
        reflexes = _build_default_reflexes(tool_registry=mock_registry)
        control = next(r for r in reflexes if r.name == "media_control")
        match = control.patterns[0].fullmatch("skip")
        result = control.handler("skip", match, {})

        call_kwargs = mock_registry.execute_tool.call_args
        assert call_kwargs[1].get("action") == "next"


# ---------------------------------------------------------------------------
# Reflex verification and fallback
# ---------------------------------------------------------------------------

class TestReflexVerification:
    def test_failed_tool_returns_failure(self, mock_registry_failing):
        reflexes = _build_default_reflexes(tool_registry=mock_registry_failing)
        play = next(r for r in reflexes if r.name == "media_play")
        match = play.patterns[0].fullmatch("play something")
        result = play.handler("play something", match, {})

        assert result.success is False
        assert result.response == ""

    def test_exception_returns_failure(self, mock_registry):
        mock_registry.execute_tool.side_effect = RuntimeError("boom")
        reflexes = _build_default_reflexes(tool_registry=mock_registry)
        play = next(r for r in reflexes if r.name == "media_play")
        match = play.patterns[0].fullmatch("play something")
        result = play.handler("play something", match, {})

        assert result.success is False

class TestPriority:
    def test_only_media_reflexes_without_social(self, mock_registry):
        reflexes = _build_default_reflexes(tool_registry=mock_registry)
        names = [r.name for r in reflexes]
        assert all(n.startswith("media_") for n in names)
        assert "greeting" not in names

    def test_no_missing_tools_crash(self):
        """Registry where some tools are missing should not crash."""
        registry = MagicMock()
        # Only media_play exists, others return None
        registry.get_tool.side_effect = lambda name: (
            MagicMock() if name == "media_play" else None
        )
        reflexes = _build_default_reflexes(tool_registry=registry)
        names = [r.name for r in reflexes]
        assert "media_play" in names
        assert "media_control" not in names
