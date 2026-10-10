"""Tier 3 saves the user turn itself, so it runs the engine's memory capture too.

A "remember that …" message that also reads as a task is deliberated on
and never reaches UnifiedChatEngine, whose capture_from_message call is the
only one the other tiers have. No model, tools or database: the executor
and the row writer are stand-ins.
"""
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from flask import Flask

from backend.services.agent_brain import AgentBrain

MESSAGE = "remember that the office printer is on the second floor, then open the printer page"


def _brain():
    brain = AgentBrain.__new__(AgentBrain)
    brain.state = SimpleNamespace(
        health=SimpleNamespace(llm_available=True, tools_available=True),
        tool_registry=MagicMock(),
        llm=SimpleNamespace(model="m"),
        max_agent_iterations=3,
        active_model="m",
    )
    return brain


def _deliberate(options, **kwargs):
    app = Flask(__name__)
    executor = MagicMock()
    executor.execute.return_value = SimpleNamespace(
        success=True, final_answer="Done.", error=None, verified=None, iterations=1,
    )
    capture = MagicMock(return_value="mem-1")
    saved = []
    with patch("backend.services.agent_executor.AgentExecutor", return_value=executor), \
            patch("backend.services.memory_capture.capture_from_message", capture), \
            patch("backend.services.agent_brain._persist_turn",
                  side_effect=lambda app, sid, role, content, *a, **k: saved.append((role, content))), \
            patch("backend.services.agent_control_service.get_agent_control_service",
                  return_value=MagicMock(drain_thinking_steps=lambda: [], drain_recipe_usage=lambda: {})), \
            patch.object(AgentBrain, "_emit_response", lambda *a, **k: None):
        result = _brain()._deliberate("sess-3", MESSAGE, options, lambda *a, **k: None, app, **kwargs)
    return result, capture, saved


def test_tier3_captures_the_remembered_fact_after_saving_the_turn():
    result, capture, saved = _deliberate({}, project_id=7)
    assert result["tier"] == 3 and result["success"]
    assert saved[0] == ("user", MESSAGE), "the question is saved before the run"
    capture.assert_called_once_with(MESSAGE, session_id="sess-3", project_id=7, explicit_only=True)


def test_an_unsaved_turn_is_not_captured_either():
    _, capture, saved = _deliberate({"persist": False})
    assert ("user", MESSAGE) not in saved
    capture.assert_not_called()


def test_skip_memory_capture_is_honoured_like_the_engine():
    _, capture, saved = _deliberate({"skip_memory_capture": True})
    assert saved[0] == ("user", MESSAGE)
    capture.assert_not_called()


def test_screen_direct_runs_the_same_capture_after_its_save():
    """With the agent screen open the brain saves the turn itself too."""
    from backend.services.step_budget import StepBudget
    brain = _brain()
    fake_acs = MagicMock()
    fake_acs.execute_task.return_value = SimpleNamespace(reason="completed", success=True, steps=[], run_id="r")
    captured = MagicMock()
    with patch("backend.services.local_screen_backend.LocalScreenBackend", MagicMock()), \
            patch("backend.services.agent_control_service.get_agent_control_service", return_value=fake_acs), \
            patch.object(AgentBrain, "_narrate_agent_outcome", return_value="ok"), \
            patch("backend.api.memory_api.get_memories_for_context", return_value=[]), \
            patch("backend.services.agent_brain._persist_turn"), \
            patch("backend.services.agent_brain._capture_memory", captured):
        brain._screen_direct("s1", MESSAGE, {}, lambda *a, **k: None, Flask(__name__),
                             budget=StepBudget(total=20), project_id=7)
    captured.assert_called_once_with(ANY, MESSAGE, "s1", project_id=7)


def test_only_the_explicit_forms_are_captured_on_the_brain_paths():
    """"My goal is to …" is a task request on these paths, not a fact to file."""
    from backend.services.agent_brain import _capture_memory
    capture = MagicMock()
    with patch("backend.services.memory_capture.capture_from_message", capture):
        _capture_memory(Flask(__name__), "my goal is to research local models", "s1")
    assert capture.call_args.kwargs["explicit_only"] is True


def test_a_capture_failure_never_stops_the_answer():
    app = Flask(__name__)
    from backend.services.agent_brain import _capture_memory
    with patch("backend.services.memory_capture.capture_from_message", side_effect=RuntimeError("db down")):
        _capture_memory(app, MESSAGE, "sess-3", project_id=None)  # logs, does not raise
