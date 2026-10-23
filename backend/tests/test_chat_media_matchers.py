"""Media commands: what each of the three matchers makes of the same message.

The brain's Tier-1 reflexes (brain_state._build_default_reflexes), the engine's
_MEDIA_PATTERNS shortcut and enhanced chat's _try_media_command each turn
"pause", "volume 30" and the like into a media tool call. They disagree on
some messages; these tests pin both live chat answers (brain reflex first,
engine shortcut when the brain is off) so a merge cannot change them silently.
"""
import pytest

from backend.services.agent_tools import BaseTool, ToolRegistry, ToolResult
from backend.services.brain_state import _build_default_reflexes
from backend.services.unified_chat_engine import UnifiedChatEngine


class _Media(BaseTool):
    description = "media"

    def __init__(self, name, log):
        self.name = name
        super().__init__()
        self._log = log

    def execute(self, **kwargs):
        self._log.append((self.name, {k: v for k, v in kwargs.items() if not k.startswith("_")}))
        return ToolResult(success=True, output="ok")


@pytest.fixture
def reflexes():
    log = []
    reg = ToolRegistry()
    for n in ("media_play", "media_control", "media_volume", "media_status"):
        reg.register(_Media(n, log))
    return _build_default_reflexes(reg), log


def _brain(reflexes, message):
    actions, log = reflexes
    log.clear()
    for r in actions:
        for p in r.patterns:
            m = p.match(message)
            if m:
                r.handler(message, m, {})
                return log[0] if log else None
    return None


def _engine(message):
    for pattern, tool, param_fn in UnifiedChatEngine._MEDIA_PATTERNS:
        m = pattern.match(message.strip())
        if m:
            return (tool, param_fn(m))
    return None


CASES = [
    # message, brain reflex, engine shortcut
    ("pause", ("media_control", {"action": "pause"}), ("media_control", {"action": "pause"})),
    ("resume", ("media_control", {"action": "play"}), ("media_control", {"action": "toggle"})),
    ("skip", ("media_control", {"action": "next"}), ("media_control", {"action": "next"})),
    ("volume 30", ("media_volume", {"level": 30}), ("media_volume", {"level": "30"})),
    ("louder", ("media_volume", {"level": "+10"}), ("media_volume", {"level": "+10"})),
    ("play some jazz", ("media_play", {"query": "some jazz"}), ("media_play", {"query": "some jazz"})),
    ("please play some jazz", None, ("media_play", {"query": "some jazz"})),
    ("stop the track", ("media_control", {"action": "stop"}), None),
]


@pytest.mark.parametrize("message,brain,engine", CASES)
def test_media_matchers(reflexes, message, brain, engine):
    assert _brain(reflexes, message) == brain
    assert _engine(message) == engine
