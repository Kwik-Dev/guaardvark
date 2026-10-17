"""The media shortcuts run media_status only for a question about the user's own
player, asked as the whole message. Both copies are checked: the unified engine's
_MEDIA_PATTERNS and enhanced chat's _try_media_command. Stub registry, no player."""

from types import SimpleNamespace

import pytest

from backend.services.unified_chat_engine import UnifiedChatEngine

OWN_PLAYER = [
    "what's playing?",
    "What's playing right now",
    "what is playing now",
    "what’s this song",
    "what's this song called?",
    "now playing",
    "current song",
]
NOT_THE_PLAYER = [
    "what's playing at the cinema tonight?",
    "what is playing in theaters this weekend",
    "what's playing on Broadway",
    "current track record of the company",
    "now playing: a review of the new album",
]


def _engine_tool(message):
    for pattern, tool_name, _params in UnifiedChatEngine._MEDIA_PATTERNS:
        if pattern.match(message.strip()):
            return tool_name
    return None


@pytest.mark.parametrize("message", OWN_PLAYER)
def test_engine_asks_the_player(message):
    assert _engine_tool(message) == "media_status"


@pytest.mark.parametrize("message", NOT_THE_PLAYER)
def test_engine_leaves_other_questions_to_the_model(message):
    assert _engine_tool(message) is None


def test_engine_player_commands_unchanged():
    assert _engine_tool("pause") == "media_control"
    assert _engine_tool("next track") == "media_control"
    assert _engine_tool("play some jazz") == "media_play"
    assert _engine_tool("volume 30") == "media_volume"


class _Registry:
    def __init__(self):
        self.calls = []

    def get_tool(self, name):
        return object()

    def execute_tool(self, name, **params):
        self.calls.append(name)
        return SimpleNamespace(success=True, output=f"{name} ran", error=None)


@pytest.fixture
def enhanced():
    from flask import Flask

    from backend.api import enhanced_chat_api as eca

    app = Flask(__name__)
    app.tool_registry = _Registry()
    manager = eca.EnhancedChatManager.__new__(eca.EnhancedChatManager)
    manager._get_or_create_session = lambda *a, **k: None
    manager._save_message = lambda *a, **k: None
    with app.app_context():
        yield manager, app.tool_registry


@pytest.mark.parametrize("message", OWN_PLAYER)
def test_enhanced_chat_asks_the_player(enhanced, message):
    manager, registry = enhanced
    assert manager._try_media_command("s1", message) is not None
    assert registry.calls == ["media_status"]


@pytest.mark.parametrize("message", NOT_THE_PLAYER)
def test_enhanced_chat_leaves_other_questions_to_the_model(enhanced, message):
    manager, registry = enhanced
    assert manager._try_media_command("s1", message) is None
    assert registry.calls == []
