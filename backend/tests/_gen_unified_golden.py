"""Generate the golden unified prompt from a fixed fixture. Run once BEFORE a
prompt-builder refactor; the test then asserts byte-equality after."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"
from unittest.mock import patch


def build():
    from backend.services.agent_control_service import (
        AgentControlService, ActionStep, AgentAction, WorldState)
    svc = AgentControlService()
    svc._pending_world_observed = "WORLD_OBSERVED: 3 elements (fixture)"
    svc._failure_reports = []
    svc._current_budget = None
    hist = [
        ActionStep(iteration=1, action=AgentAction(action_type="click", target_description="Firefox icon"), failed=False),
        ActionStep(iteration=2, action=AgentAction(action_type="scroll", scroll_amount=3), failed=True),
        ActionStep(iteration=3, action=AgentAction(action_type="scroll", scroll_amount=3), failed=True),
    ]
    ws = WorldState(timestamp_iso="2026-09-22T00:00:00", desktop_state="Firefox (1 window)",
                    dom_url="https://example.com", dom_title="Example", dom_element_count=3,
                    cursor_pos=(10, 20), last_action="scroll", last_action_status="FAIL",
                    scene_hint="a page", progress_label="partial_progress", progress_confidence=0.4,
                    progress_evidence="fixture", progress_next_hint="try End", blocked_actions="",
                    current_subgoal="open page", next_subgoal="read heading", subgoal_completion_signal="heading visible")
    with patch.object(AgentControlService, "_get_desktop_state", staticmethod(lambda display=None: "Desktop state: Firefox window (fixture)")), \
         patch.object(AgentControlService, "_format_dom_grounding_for_prompt", lambda self: "Interactive elements on this page:\n  [1] button \"Go\"\n\n"):
        return svc._build_unified_prompt("open youtube and find the comments", hist, world_state=ws,
                                         training_mode=True, chat_context="user said hi")


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "fixtures", "unified_prompt_golden.txt")
    text = build()
    with open(out, "w") as f:
        f.write(text)
    print(f"wrote {out} ({len(text)} chars)")
