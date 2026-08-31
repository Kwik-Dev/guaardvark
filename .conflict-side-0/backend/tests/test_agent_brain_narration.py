"""The narration fallback asks a thinking model for an answer, not reasoning."""
from types import SimpleNamespace
from unittest.mock import patch

from backend.services.agent_brain import AgentBrain


class _FakeClient:
    calls = []

    def __init__(self, *a, **kw):
        pass

    def chat(self, **kw):
        _FakeClient.calls.append(kw)
        yield {"message": {"content": "Opened the page you asked for."}}


def _brain(model):
    brain = AgentBrain.__new__(AgentBrain)
    brain.state = SimpleNamespace(active_model=model)
    return brain


def _narrate(model):
    _FakeClient.calls = []
    events = []
    with patch("ollama.Client", _FakeClient), \
            patch("backend.services.agent_brain.is_aborted", return_value=False), \
            patch("backend.utils.ollama_resource_manager.get_model_info", return_value=None):
        text = _brain(model)._narrate_agent_outcome(
            "open the docs", SimpleNamespace(reason="completed", success=True, steps=[]),
            "", lambda name, payload: events.append((name, payload)), "sess-narrate",
        )
    return text, _FakeClient.calls, events


def test_thinking_model_is_asked_with_thinking_off():
    text, calls, events = _narrate("gemma4:12b")
    assert text == "Opened the page you asked for."
    assert calls[0]["think"] is False
    assert calls[0]["options"]["num_predict"] == 800
    assert events and events[0][0] == "chat:token"


def test_other_models_get_no_think_flag():
    _, calls, _ = _narrate("llama3:latest")
    assert "think" not in calls[0]
