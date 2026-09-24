#!/usr/bin/env python3

import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"


class TestAgentAction(unittest.TestCase):

    def test_agent_action_click(self):
        from backend.services.agent_control_service import AgentAction
        action = AgentAction(action_type="click", target_cell="D4", target_description="Tweet button")
        self.assertEqual(action.action_type, "click")
        self.assertEqual(action.target_cell, "D4")

    def test_agent_action_type_text(self):
        from backend.services.agent_control_service import AgentAction
        action = AgentAction(action_type="type", text="hello")
        self.assertEqual(action.action_type, "type")
        self.assertEqual(action.text, "hello")


class TestAgentControlConfig(unittest.TestCase):

    def test_default_config(self):
        from backend.services.agent_control_service import AgentControlConfig
        config = AgentControlConfig()
        # Ceilings, not the normal exit: the loop stops on the stall rule.
        self.assertEqual(config.max_iterations, 40)
        self.assertEqual(config.task_timeout_seconds, 480)
        self.assertEqual(config.max_stall_steps, 4)
        self.assertEqual(config.verify_actions, True)
        self.assertEqual(config.grid_cols, 8)
        self.assertEqual(config.grid_rows, 8)
        self.assertEqual(config.vision_model, "gemma4:e4b")
        self.assertEqual(config.max_consecutive_failures, 5)
        self.assertFalse(config.prior_run_note_enabled, "the look-back line is opt-in until measured")


class TestAgentModeState(unittest.TestCase):

    def setUp(self):
        # Reset singleton between tests (follows BrowserAutomationService pattern)
        import backend.services.agent_control_service as acs
        acs._service_instance = None

    def test_initial_state_is_inactive(self):
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        self.assertFalse(service.is_active)

    def test_start_sets_active(self):
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        service._active = True
        self.assertTrue(service.is_active)

    def test_kill_switch(self):
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        service._active = True
        service.kill()
        self.assertFalse(service.is_active)
        self.assertTrue(service._killed)


class TestBuildVisionPrompt(unittest.TestCase):

    def test_builds_scene_analysis_prompt(self):
        from backend.services.agent_control_service import AgentControlService
        service = AgentControlService()
        prompt = service._build_vision_prompt("Post hello to Twitter", [])
        self.assertIn("describe the screen", prompt.lower())
        self.assertIn("interactive element", prompt.lower())

    def test_includes_task_context(self):
        from backend.services.agent_control_service import AgentControlService
        service = AgentControlService()
        prompt = service._build_vision_prompt("Post hello to Twitter", [])
        self.assertIn("Post hello to Twitter", prompt)


class TestParseDecision(unittest.TestCase):

    def test_parse_click_decision(self):
        from backend.services.agent_control_service import AgentControlService
        service = AgentControlService()
        llm_output = '{"action": "click", "target_cell": "D4", "target_description": "Tweet button", "reasoning": "Need to click Tweet"}'
        decision = service._parse_decision(llm_output)
        self.assertEqual(decision.action.action_type, "click")
        self.assertEqual(decision.action.target_cell, "D4")

    def test_parse_type_decision(self):
        from backend.services.agent_control_service import AgentControlService
        service = AgentControlService()
        llm_output = '{"action": "type", "text": "Hello world", "reasoning": "Typing message"}'
        decision = service._parse_decision(llm_output)
        self.assertEqual(decision.action.action_type, "type")
        self.assertEqual(decision.action.text, "Hello world")

    def test_parse_done_decision(self):
        from backend.services.agent_control_service import AgentControlService
        service = AgentControlService()
        llm_output = '{"action": "done", "reasoning": "Task completed successfully"}'
        decision = service._parse_decision(llm_output)
        self.assertTrue(decision.task_complete)

    def test_parse_done_with_weak_proof_and_prior_verified_step(self):
        """Documents + lightly exercises the grounding + advisory done path (core of the GOTHAM RISING fix).
        has_recent_verified + grounding from ActionStep.result + advisory (non-failed) handling live in
        execute_task done block (see 820-904 area + hoisted detection + proof grounding). The parser
        surfaces success_proof; full advisory contract + "done (advisory...)" emit exercised via e2e mocks
        and manual ChatPage+AgentScreen runs per plan verification section.
        """
        from backend.services.agent_control_service import AgentControlService, ActionStep, AgentAction
        service = AgentControlService()
        # Simulate history after a servo-verified click on the specific target (DPC "verified":True
        # or post_action_effect containing "verified" — exactly what click_target + fast path produce).
        prior_step = ActionStep(
            iteration=0,
            scene_description="youtube search results with thumbnails",
            action=AgentAction(action_type="click", target_description="GOTHAM RISING video thumbnail"),
            result={"success": True, "verified": True, "post_action_effect": "verified", "verifier": "servo_region_dpc"},
            failed=False,
        )
        service._action_history = [prior_step]
        # Model emits done with weak/empty proof (the case that previously hard-rejected even after goal achieved).
        llm_output = '{"action": "done", "success_proof": "", "reasoning": "I clicked the video and the player is now there"}'
        decision = service._parse_decision(llm_output)
        self.assertTrue(decision.task_complete)
        # In execute_task the has_recent_verified block would have grounded the proof from the prior target
        # and taken the advisory (non-failure) path instead of "done rejected — proof not visible".
        # We assert the objects here; runtime advisory/grounding covered by higher-level tests + manual.

    def test_parse_invalid_json_returns_stuck(self):
        from backend.services.agent_control_service import AgentControlService
        service = AgentControlService()
        decision = service._parse_decision("not valid json at all")
        self.assertTrue(decision.stuck)


class TestGetStatus(unittest.TestCase):

    def test_status_returns_dict(self):
        from backend.services.agent_control_service import get_agent_control_service
        service = get_agent_control_service()
        status = service.get_status()
        self.assertIn("active", status)
        self.assertIn("killed", status)
        self.assertIn("current_task", status)
        self.assertIn("iteration", status)


if __name__ == "__main__":
    unittest.main()


def _click(target, ok=True, action_type="click"):
    from backend.services.agent_control_service import ActionStep, AgentAction
    return ActionStep(action=AgentAction(action_type=action_type, target_description=target), failed=not ok)


def _prompt_svc():
    from backend.services.agent_control_service import AgentControlService
    svc = AgentControlService()
    svc._pending_world_observed = ""
    svc._failure_reports = []
    svc._current_budget = None
    return svc


class TestTaskMemory(unittest.TestCase):
    """The model must see every step of the current task.

    2026-09-23: shown only its last three steps, gemma4:12b clicked A, B, C, D
    perfectly and then cycled A-B-C-D until the timeout, never reaching E,
    because the target that had just scrolled out of the window looked
    pending again. These pin the full list, its counts and the repeat note.
    """

    def setUp(self):
        from backend.services.agent_control_service import AgentControlService
        self.A = AgentControlService

    def test_empty_history_renders_nothing(self):
        self.assertEqual(self.A._history_block([], 15), "")

    def test_every_step_listed_in_order_with_counts(self):
        hist = [_click(f"dot {c}") for c in "ABCDEF"]
        block = self.A._history_block(hist, 15)
        lines = block.splitlines()
        self.assertEqual(lines[0], "Done (steps: 6, click attempts: 6):")
        self.assertEqual(lines[1:], [f"  click: dot {c} [OK]" for c in "ABCDEF"])

    def test_cap_truncates_and_says_so(self):
        hist = [_click(f"dot {i}") for i in range(20)]
        block = self.A._history_block(hist, 15)
        lines = block.splitlines()
        self.assertEqual(lines[0], "Done (steps: 20, click attempts: 20; showing last 15):")
        self.assertEqual(len(lines), 16)
        self.assertEqual(lines[1], "  click: dot 5 [OK]")

    def test_click_attempts_count_only_the_click_family(self):
        from backend.services.agent_control_service import ActionStep, AgentAction
        hist = [
            _click("Firefox icon"),
            ActionStep(action=AgentAction(action_type="scroll", scroll_amount=3), failed=True),
            ActionStep(action=AgentAction(action_type="type", text="hello")),
            _click("Post button", ok=False),
        ]
        block = self.A._history_block(hist, 15)
        self.assertTrue(block.startswith("Done (steps: 4, click attempts: 2):"))

    def test_per_line_format_is_the_golden_fixtures(self):
        from backend.services.agent_control_service import ActionStep, AgentAction
        hist = [_click("Firefox icon"),
                ActionStep(action=AgentAction(action_type="scroll", scroll_amount=3), failed=True)]
        lines = self.A._history_block(hist, 15).splitlines()
        self.assertEqual(lines[1], "  click: Firefox icon [OK]")
        self.assertEqual(lines[2], "  scroll:  [FAIL]")

    def test_repeat_block_is_empty_without_a_repeat(self):
        from backend.services.agent_control_service import ActionStep, AgentAction
        hist = [_click("Firefox icon"),
                ActionStep(action=AgentAction(action_type="scroll", scroll_amount=3), failed=True),
                ActionStep(action=AgentAction(action_type="scroll", scroll_amount=3), failed=True)]
        self.assertEqual(self.A._repeat_block(hist), "")

    def test_repeat_block_names_the_repeated_target_and_count(self):
        hist = [_click("red dot A"), _click("blue dot B"), _click("green dot C"),
                _click("orange dot D"), _click("red dot A")]
        block = self.A._repeat_block(hist)
        self.assertIn('"red dot A" x2', block)
        for other in ("blue dot B", "green dot C", "orange dot D"):
            self.assertNotIn(other, block)

    def test_a_failed_attempt_then_a_hit_is_not_a_repeat(self):
        hist = [_click("red dot A", ok=False), _click("red dot A")]
        self.assertEqual(self.A._repeat_block(hist), "")

    def test_repeat_key_ignores_case_and_whitespace(self):
        hist = [_click("Red dot A"), _click(" red dot a ")]
        self.assertIn('"Red dot A" x2', self.A._repeat_block(hist))

    def test_unified_prompt_at_step_five_shows_all_four_targets(self):
        from unittest.mock import patch
        svc = _prompt_svc()
        hist = [_click(t) for t in ("red dot A", "blue dot B", "green dot C", "orange dot D")]
        with patch.object(self.A, "_get_desktop_state", staticmethod(lambda display=None: "Desktop: fixture")), \
             patch.object(self.A, "_format_dom_grounding_for_prompt", lambda self: ""):
            p = svc._build_unified_prompt("click once in each of the dots", hist)
        for t in ("red dot A", "blue dot B", "green dot C", "orange dot D"):
            self.assertIn(f"  click: {t} [OK]", p)
        self.assertIn("Step 5.", p)
        self.assertIn("Done (steps: 4, click attempts: 4):", p)

    def test_repeat_note_is_suppressed_in_training_mode(self):
        from unittest.mock import patch
        svc = _prompt_svc()
        hist = [_click("colored circle") for _ in range(3)]
        with patch.object(self.A, "_get_desktop_state", staticmethod(lambda display=None: "Desktop: fixture")), \
             patch.object(self.A, "_format_dom_grounding_for_prompt", lambda self: ""):
            training = svc._build_unified_prompt("practice", hist, training_mode=True)
            normal = svc._build_unified_prompt("practice", hist, training_mode=False)
        self.assertNotIn("Already clicked", training)
        self.assertIn('Already clicked [OK] more than once: "colored circle" x3', normal)

    def test_history_cap_is_the_configured_max_iterations(self):
        from unittest.mock import patch
        svc = _prompt_svc()
        svc.config.max_iterations = 4
        hist = [_click(f"dot {i}") for i in range(6)]
        with patch.object(self.A, "_get_desktop_state", staticmethod(lambda display=None: "Desktop: fixture")), \
             patch.object(self.A, "_format_dom_grounding_for_prompt", lambda self: ""):
            p = svc._build_unified_prompt("t", hist)
        self.assertIn("showing last 4", p)


def _stepped(target, ok=True, action_type="click", verified=False, effect=None):
    from backend.services.agent_control_service import ActionStep, AgentAction
    result = {"success": ok, "verified": verified}
    if effect:
        result["post_action_effect"] = effect
    return ActionStep(action=AgentAction(action_type=action_type, target_description=target),
                      result=result, failed=not ok)


class TestStallRule(unittest.TestCase):
    """The loop stops when progress stops, not at a fixed count.

    Progress: a verified screen change, or a click on a target not yet clicked
    [OK] this task. Failed steps have their own guard and do not count.
    """

    def setUp(self):
        from backend.services.agent_control_service import AgentControlService
        self.A = AgentControlService
        self.svc = AgentControlService()

    def _run(self, steps, training_mode=False):
        """Feed steps through the loop's own counter; return the 1-based step
        at which it says stop, or None."""
        self.svc._action_history = []
        self.svc._stall_steps = 0
        for i, st in enumerate(steps, 1):
            self.svc._action_history.append(st)
            if self.svc._note_progress(st, training_mode=training_mode):
                return i
        return None

    def test_progress_truth_table(self):
        A = _stepped("red dot A")
        self.assertTrue(self.A._step_progress(A, []))
        self.assertFalse(self.A._step_progress(_stepped("red dot A"), [A]), "repeat, no change")
        self.assertTrue(self.A._step_progress(_stepped("red dot A", verified=True), [A]), "repeat with a change")
        self.assertTrue(self.A._step_progress(_stepped("red dot A", effect="verified"), [A]))
        self.assertFalse(self.A._step_progress(_stepped("red dot A", ok=False), []), "failed never counts")
        self.assertTrue(self.A._step_progress(_stepped("", action_type="type", verified=True), []))
        self.assertFalse(self.A._step_progress(_stepped("", action_type="type"), []))
        self.assertFalse(self.A._step_progress(_stepped("", action_type="wait"), []))
        self.assertTrue(self.A._step_progress(_stepped("Red Dot A "), [_stepped("red dot a", ok=False)]),
                        "a target only ever missed is still new")

    def test_replays_the_2026_09_23_cycle_and_stops_at_step_nine(self):
        # run 242cd07c: A B C D E, then A B C D E, A, B — all hits, none changed the screen.
        steps = [_stepped(t) for t in ("A", "B", "C", "D", "E", "A", "B", "C", "D", "E", "A", "B")]
        self.assertEqual(self._run(steps), 9)
        self.assertEqual(self.svc._stall_steps, 4)

    def test_five_new_targets_never_stall(self):
        self.assertIsNone(self._run([_stepped(t) for t in "ABCDE"]))
        self.assertEqual(self.svc._stall_steps, 0)

    def test_a_verified_change_resets_the_counter(self):
        steps = [_stepped("A"), _stepped("A"), _stepped("A"), _stepped("A", verified=True),
                 _stepped("A"), _stepped("A"), _stepped("A")]
        self.assertIsNone(self._run(steps))
        self.assertEqual(self.svc._stall_steps, 3)

    def test_failed_steps_do_not_count_toward_a_stall(self):
        steps = [_stepped("A")] + [_stepped("B", ok=False)] * 6
        self.assertIsNone(self._run(steps))

    def test_training_mode_never_stalls(self):
        self.assertIsNone(self._run([_stepped("colored circle")] * 20, training_mode=True))

    def test_the_threshold_is_the_configured_one(self):
        self.svc.config.max_stall_steps = 2
        self.assertEqual(self._run([_stepped("A"), _stepped("A"), _stepped("A")]), 3)

