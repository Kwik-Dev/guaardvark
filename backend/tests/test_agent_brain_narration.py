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


def test_a_stalled_run_has_its_own_words():
    text = _brain("m")._fallback_outcome_text("stalled_no_progress", False)
    assert "stopped" in text and "loop" in text


def test_screen_direct_hands_the_loop_the_budget_without_a_step_cap():
    """The chat path used to cap the screen loop at min(budget, 12) steps and
    put "[BUDGET: n/20]" in its context; the loop now stops on its own rule."""
    from unittest.mock import MagicMock
    from backend.services.step_budget import StepBudget
    brain = _brain("gemma4:12b")
    fake_acs = MagicMock()
    fake_acs.execute_task.return_value = SimpleNamespace(reason="completed", success=True, steps=[], run_id="r")
    budget = StepBudget(total=20)
    with patch("backend.services.local_screen_backend.LocalScreenBackend", MagicMock()), \
            patch("backend.services.agent_control_service.get_agent_control_service", return_value=fake_acs), \
            patch.object(AgentBrain, "_narrate_agent_outcome", return_value="ok"), \
            patch("backend.api.memory_api.get_memories_for_context", return_value=[]):
        brain._screen_direct("s1", "click the red dot", {}, lambda *a, **k: None, None, budget=budget)
    kwargs = fake_acs.execute_task.call_args.kwargs
    assert "max_steps" not in kwargs
    assert kwargs["budget"] is budget and kwargs["session_id"] == "s1"
    assert "[BUDGET:" not in kwargs["chat_context"]
    assert budget.used == 2, "direct entry plus the context query, never one per step"
