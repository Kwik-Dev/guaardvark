"""Every screen task is an episode: one run row, one row per step, readable back."""
import os
import sys
import time

import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask
from backend.models import db, AgentTaskRun, AgentTaskStep
from backend.api.agent_control_api import agent_control_bp


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(agent_control_bp)
    with app.app_context(), patch("backend.config.GUAARDVARK_ROOT", str(tmp_path)):
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _svc():
    from backend.services.agent_control_service import AgentControlService, BrainEye
    svc = AgentControlService()
    svc._brain_eye = BrainEye("gemma4:12b", "gemma4:12b", True, "native", "fixture")
    svc._task_started_at = time.time() - 40
    return svc


def _result(targets, success=True, reason="completed", extra_result=None):
    from backend.services.agent_control_service import AgentResult, ActionStep, AgentAction
    steps = []
    for i, t in enumerate(targets):
        r = {"success": True, "verified": False, "post_action_effect": "no_visible_change",
             "x": 10 * i, "y": 20 * i, "attempt": 1}
        if extra_result:
            r.update(extra_result)
        steps.append(ActionStep(
            iteration=i,
            action=AgentAction(action_type="click", target_description=t, reasoning=f"next is {t}",
                               coordinates=(10 * i, 20 * i)),
            result=r, failed=False))
    return AgentResult(success=success, reason=reason, steps=steps, total_time_seconds=40.0)


def test_persist_writes_the_run_and_every_step(app):
    svc = _svc()
    res = _result(["red dot A", "blue dot B", "green dot C", "orange dot D", "purple dot E"])
    run_id = svc._persist_task_run(res, task_id="t1", task="click each dot")
    assert run_id and res.run_id == run_id
    run = db.session.get(AgentTaskRun, run_id)
    assert run.steps_count == 5 and run.click_attempts == 5
    assert run.success is True and run.reason == "completed" and run.stop_rule == "done"
    assert run.brain == "gemma4:12b" and run.unified is True and run.mode == "api"
    steps = AgentTaskStep.query.filter_by(run_id=run_id).order_by(AgentTaskStep.iteration).all()
    assert [s.target for s in steps] == ["red dot A", "blue dot B", "green dot C", "orange dot D", "purple dot E"]
    assert steps[1].coordinates == [10, 20] and steps[1].servo_attempt == 1
    assert steps[4].reasoning == "next is purple dot E"


def test_non_json_result_values_are_stored_sanitised(app):
    import numpy as np
    svc = _svc()
    res = _result(["red dot A"], extra_result={"delta": np.float32(0.5), "box": (1, 2)})
    run_id = svc._persist_task_run(res, task_id="t2", task="click A")
    step = AgentTaskStep.query.filter_by(run_id=run_id).one()
    assert step.result["box"] == [1, 2]
    assert isinstance(step.result["delta"], str)


def test_a_failed_write_never_blocks_the_result(app):
    svc = _svc()
    res = _result(["red dot A"])
    with patch.object(db.session, "commit", side_effect=RuntimeError("db away")):
        run_id = svc._persist_task_run(res, task_id="t3", task="click A")
    assert run_id == "" and res.run_id == ""
    assert res.success is True and len(res.steps) == 1


def test_stop_rule_is_derived_from_the_reason():
    from backend.services.agent_control_service import AgentControlService as A
    assert A._stop_rule_from_reason("completed") == "done"
    assert A._stop_rule_from_reason("completed (firefox is now open)") == "early_done"
    assert A._stop_rule_from_reason("completed_with_repetition") == "loop"
    assert A._stop_rule_from_reason("timeout") == "timeout"
    assert A._stop_rule_from_reason("max_iterations") == "ceiling"
    assert A._stop_rule_from_reason("stalled_no_progress") == "stalled"
    assert A._stop_rule_from_reason("error: boom") == "error"
    assert A._stop_rule_from_reason("recipe:open_firefox") == "recipe"
    assert A._stop_rule_from_reason("") == "other"


def test_mode_follows_session_and_training(app):
    svc = _svc()
    svc._task_session_id = "chat-1"
    run_id = svc._persist_task_run(_result(["a"]), task_id="t", task="x")
    assert db.session.get(AgentTaskRun, run_id).mode == "chat"
    assert db.session.get(AgentTaskRun, run_id).session_id == "chat-1"
    svc._training_mode = True
    run_id = svc._persist_task_run(_result(["a"]), task_id="t", task="x")
    assert db.session.get(AgentTaskRun, run_id).mode == "training"


def test_prior_run_note_after_a_failure_names_the_targets(app):
    svc = _svc()
    svc._persist_task_run(_result(["red dot A", "blue dot B", "red dot A", "blue dot B"],
                                  success=False, reason="timeout"),
                          task_id="t", task="click each dot")
    note = svc._prior_run_note_for("Click each dot")
    assert note.startswith("The last attempt at this exact task (")
    assert 'ended "timeout" after 4 actions without finishing' in note
    assert "red dot A" not in note, "never list targets: the model reads a list as a script"
    assert note.endswith("The Done list below is THIS attempt only; do not redo work it already shows.")


def test_prior_run_note_after_a_success_counts_but_never_lists_the_steps(app):
    """Live 2026-09-23 20:43: a listed trace was replayed as a script after the
    task was already complete. Counts only."""
    from backend.services.agent_control_service import ActionStep, AgentAction
    svc = _svc()
    res = _result(["red dot A", "blue dot B"])
    res.steps.append(ActionStep(iteration=2, action=AgentAction(action_type="wait_until_visible",
                                                                 target_description="proof"), failed=True))
    svc._persist_task_run(res, task_id="t", task="click each dot")
    note = svc._prior_run_note_for("click each dot")
    assert note.startswith("This exact task succeeded before (")
    assert "2 actions" in note
    assert "red dot A" not in note and "click" not in note.split("succeeded before")[1].split("actions")[0]
    assert "THIS attempt only" in note


def test_prior_run_note_is_empty_without_a_run(app):
    assert _svc()._prior_run_note_for("never seen") == ""


def test_prior_run_note_reaches_both_prompts(app):
    from backend.services.agent_control_service import AgentControlService
    svc = _svc()
    svc._pending_world_observed = ""
    svc._failure_reports = []
    svc._current_budget = None
    svc._prior_run_note = "Last attempt at this exact task (fixture) ended \"timeout\" after 4 steps."
    with patch.object(AgentControlService, "_get_desktop_state", staticmethod(lambda display=None: "Desktop: fixture")), \
         patch.object(AgentControlService, "_format_dom_grounding_for_prompt", lambda self: ""):
        unified = svc._build_unified_prompt("click each dot", [])
        split = svc._build_decision_prompt("click each dot", "five dots", [])
    assert svc._prior_run_note in unified and svc._prior_run_note in split
    assert unified.index(svc._prior_run_note) < unified.index("Step 1.")


def test_routes_list_and_read_runs(app, client):
    svc = _svc()
    first = svc._persist_task_run(_result(["a"]), task_id="t", task="first")
    time.sleep(0.01)
    svc._task_started_at = time.time()
    second = svc._persist_task_run(_result(["b", "c"]), task_id="t", task="second")
    listing = client.get("/api/agent-control/runs?limit=5").get_json()
    assert listing["success"] and [r["id"] for r in listing["runs"]] == [second, first]
    assert "steps" not in listing["runs"][0]
    one = client.get(f"/api/agent-control/runs/{second}").get_json()
    assert one["run"]["task"] == "second"
    assert [s["target"] for s in one["run"]["steps"]] == ["b", "c"]
    assert client.get("/api/agent-control/runs/nope").status_code == 404
