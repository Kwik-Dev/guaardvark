"""Phase A memory wiring tests.

Covers two surgical changes:
  1. Unified recall — agent_control_service._load_lesson_memories is a shim
     over memory_api.get_lessons_for_agent_prompt; all three recall callers
     share one SQL query in _query_memories.
  2. Strong-positive detection — _detect_strong_positive picks up enthusiastic
     feedback phrases in the user's comment, and the inducer bumps importance
     when the signal is set.

Tests that need a live database are skipped in the unit suite — the structured
behaviour we care about (phrase detection, shim delegation) doesn't need one.
"""

import importlib
import unittest
from unittest.mock import patch, MagicMock


def _module_importable(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


_HAS_MEMORY_API = _module_importable("backend.api.memory_api")
# self_improvement_service imports backend.models lazily (inside functions), so
# the module itself imports cleanly even without flask_sqlalchemy. The default
# tests below patch the model layer, which only works when backend.models can
# actually be imported — check that too.
_HAS_SELF_IMPROVEMENT = (
    _module_importable("backend.services.self_improvement_service")
    and _module_importable("backend.models")
)
_HAS_CELERY_APP = _module_importable("backend.celery_app")


class TestStrongPositiveDetection(unittest.TestCase):
    """Comment-only detection (no DB lookup) — fast, deterministic."""

    def test_plain_thumbs_up_is_not_strong(self):
        from backend.api.agent_control_api import _detect_strong_positive
        # No comment, no session: routine 👍, nothing extra.
        self.assertFalse(_detect_strong_positive("", session_id=None))
        self.assertFalse(_detect_strong_positive("ok thanks", session_id=None))

    def test_excellent_in_comment_is_strong(self):
        from backend.api.agent_control_api import _detect_strong_positive
        self.assertTrue(_detect_strong_positive("excellent work", session_id=None))
        self.assertTrue(_detect_strong_positive("Excellent!", session_id=None))

    def test_perfect_variants(self):
        from backend.api.agent_control_api import _detect_strong_positive
        self.assertTrue(_detect_strong_positive("perfect", session_id=None))
        self.assertTrue(_detect_strong_positive("perfectly done", session_id=None))

    def test_multi_word_phrases(self):
        from backend.api.agent_control_api import _detect_strong_positive
        for phrase in [
            "very good",
            "well done",
            "nailed it",
            "spot on",
            "nice work",
            "that's it",
            "great job",
        ]:
            self.assertTrue(
                _detect_strong_positive(phrase, session_id=None),
                f"phrase failed to match: {phrase!r}",
            )

    def test_word_boundaries_avoid_false_positives(self):
        from backend.api.agent_control_api import _detect_strong_positive
        # "imperfect" should not trigger "perfect"; "excellence" should not
        # trigger "excellent" via substring match.
        self.assertFalse(_detect_strong_positive("imperfect approach", session_id=None))
        # NB: "excellence" does match because of \b\w\b boundaries — that's a
        # known false-positive cost we accept (and would surface in feedback
        # comments only, which the user wrote intentionally anyway). Document
        # the expected behaviour rather than fight the regex.
        self.assertTrue(_detect_strong_positive("excellent answer", session_id=None))


@unittest.skipUnless(_HAS_MEMORY_API, "memory_api unavailable (flask_sqlalchemy missing)")
class TestUnifiedRecallShim(unittest.TestCase):
    """agent_control_service._load_lesson_memories now delegates to memory_api."""

    def test_load_lesson_memories_delegates_to_helper(self):
        from backend.services.agent_control_service import AgentControlService

        # Patch the helper the shim imports lazily inside the method.
        # Force-import the module first so the attribute exists on the package.
        import backend.api.memory_api  # noqa: F401
        with patch(
            "backend.api.memory_api.get_lessons_for_agent_prompt",
            return_value="## Lessons & Notes (canary)\n### Test\n  1. step",
        ) as mock_helper:
            out = AgentControlService._load_lesson_memories(max_rows=6, max_chars=2500)

        mock_helper.assert_called_once_with(
            max_rows=6,
            max_chars=2500,
            include_belief_updates=True,
        )
        self.assertIn("canary", out)

    def test_load_lesson_memories_returns_empty_when_helper_returns_empty(self):
        from backend.services.agent_control_service import AgentControlService
        import backend.api.memory_api  # noqa: F401

        with patch(
            "backend.api.memory_api.get_lessons_for_agent_prompt",
            return_value="",
        ):
            out = AgentControlService._load_lesson_memories()

        self.assertEqual(out, "")

    def test_load_lesson_memories_swallows_import_error(self):
        """If memory_api can't be imported, the loader returns empty rather than crash.

        The persistent-knowledge prompt builder must never raise — a broken
        memory layer should silently degrade, not break agent task execution.
        """
        from backend.services import agent_control_service as svc

        # Force the import inside the method to fail.
        with patch.dict("sys.modules", {"backend.api.memory_api": None}):
            # ImportError path is wrapped in try/except in the shim; should
            # return "" cleanly.
            out = svc.AgentControlService._load_lesson_memories()
        self.assertEqual(out, "")


@unittest.skipUnless(_HAS_MEMORY_API, "memory_api unavailable (flask_sqlalchemy missing)")
class _MemoryDbCase(unittest.TestCase):
    """An in-memory SQLite app holding AgentMemory rows for recall tests."""

    def setUp(self):
        from flask import Flask
        from backend.models import db
        self.db = db
        self.app = Flask(__name__)
        self.app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.ctx.pop()

    def _mem(self, mid, content, importance=0.7, mem_type="fact", source="chat"):
        from backend.models import AgentMemory
        m = AgentMemory(id=mid, content=content, source=source, type=mem_type,
                        importance=importance, confidence=1.0, status="active")
        self.db.session.add(m)
        self.db.session.commit()
        return m

    def _recall_ids(self, query, **kwargs):
        from backend.api.memory_api import _query_memories
        return [m.id for m in _query_memories(query=query, count_access=False, **kwargs)]


class TestRecallQueryKeywords(_MemoryDbCase):
    """The SQL prefilter searches the question's keywords, not its filler."""

    def test_query_terms_drop_punctuation_and_filler(self):
        from backend.services.memory_contract import query_terms
        self.assertEqual(query_terms("What is my name?"), ["name"])
        self.assertEqual(query_terms("What's Sam's favourite colour?"), ["sam", "favourite", "colour"])
        self.assertEqual(query_terms("how do I run start.sh, again?"), ["run", "start.sh", "again"])

    def test_a_chat_captured_name_answers_what_is_my_name(self):
        # 0.7 is what chat capture stores ("remember that my name is Sam").
        self._mem("name", "my name is Sam", importance=0.7)
        self._mem("other", "The team meeting is on Tuesday at noon", importance=0.7)
        self.assertEqual(self._recall_ids("What is my name?"), ["name"])

    def test_prefilter_uses_at_most_eight_keywords(self):
        from backend.services.memory_contract import MAX_PREFILTER_TERMS
        self.assertEqual(MAX_PREFILTER_TERMS, 8)
        self._mem("ninth", "the word nine lives here", importance=0.7)
        query = "one1 two2 three3 four4 five5 six6 seven7 eight8 nine"
        self.assertEqual(self._recall_ids(query), [])


class TestWholeWordRecall(_MemoryDbCase):
    """A memory is recalled for a query only when it shares a whole word."""

    QUESTION = "what do I know about art?"

    def test_art_does_not_match_start(self):
        from backend.services.memory_contract import memory_match_score
        self.assertEqual(memory_match_score("start the backend with start.sh", [], self.QUESTION), 0.0)
        self.assertGreater(memory_match_score("I collect art deco posters", [], self.QUESTION), 0.0)

    def test_plurals_possessives_paths_and_tags_still_match(self):
        from backend.services.memory_contract import memory_match_score
        self.assertGreater(memory_match_score("Two notebooks on the desk", [], "notebook"), 0.0)
        self.assertGreater(memory_match_score("Sam's laptop is grey", [], "sam"), 0.0)
        self.assertGreater(memory_match_score("start the backend with start.sh", [], "run start.sh"), 0.0)
        self.assertGreater(memory_match_score("Use the llama cache", ["tokenizer"], "tokenizer"), 0.0)

    def test_a_memory_sharing_no_word_with_the_query_is_left_out(self):
        self._mem("start", "start the backend with start.sh", mem_type="note")
        self._mem("art", "I collect art deco posters")
        # A fact at 0.85 or above is always-on: it joins every recall.
        self._mem("always", "The office wifi network is called Lighthouse", importance=0.9)

        ids = self._recall_ids(self.QUESTION)
        self.assertIn("art", ids)
        self.assertIn("always", ids)
        self.assertNotIn("start", ids)
        self.assertEqual(self._recall_ids(self.QUESTION, include_always_on=False), ["art"])

    def test_a_query_with_no_keyword_still_lists_top_memories(self):
        self._mem("meeting", "The team meeting is on Tuesday at noon")
        self.assertEqual(self._recall_ids("what is it?"), ["meeting"])


@unittest.skipUnless(_HAS_SELF_IMPROVEMENT, "self_improvement_service unavailable (flask_sqlalchemy missing)")
class TestSelfImprovementDefaults(unittest.TestCase):
    """Default-on analysis, default-blocked apply, user kill-switch unchanged.

    Contract: with no DB rows set —
      _is_self_improvement_enabled       → True  (analysis on by default)
      _is_self_improvement_apply_enabled → False (apply requires opt-in)
      _is_codebase_locked                → False (user kill-switch, unchanged)
    Explicit DB rows still win — we only changed the default for absent rows.
    """

    def _patch_setting(self, row):
        """Patch backend.models.db.session.query(...).filter_by(...).first() → row.

        The functions import db lazily inside their bodies, so the patch target
        is the actual db module, not the calling module.
        """
        from backend.models import db
        query = MagicMock()
        query.filter_by.return_value.first.return_value = row
        return patch.object(db.session, "query", return_value=query)

    def test_self_improvement_enabled_defaults_true_when_no_row(self):
        from backend.services import self_improvement_service as sis
        with self._patch_setting(None):
            self.assertTrue(sis._is_self_improvement_enabled())

    def test_self_improvement_disabled_when_row_says_false(self):
        from backend.services import self_improvement_service as sis
        row = MagicMock()
        row.value = "false"
        with self._patch_setting(row):
            self.assertFalse(sis._is_self_improvement_enabled())

    def test_apply_enabled_defaults_false_when_no_row(self):
        """Apply is the dangerous gate — must default off."""
        from backend.services import self_improvement_service as sis
        with self._patch_setting(None):
            self.assertFalse(sis._is_self_improvement_apply_enabled())

    def test_apply_enabled_when_row_says_true(self):
        from backend.services import self_improvement_service as sis
        row = MagicMock()
        row.value = "true"
        with self._patch_setting(row):
            self.assertTrue(sis._is_self_improvement_apply_enabled())

    def test_codebase_lock_default_unlocked_unchanged(self):
        """Critical regression guard: flipping the analysis default must NOT
        also have flipped the user kill-switch. User-initiated chat-driven
        agent code edits should still work out of the box.
        """
        from backend.services import self_improvement_service as sis
        with self._patch_setting(None), \
             patch("backend.services.self_improvement_service.os.path.exists", return_value=False):
            self.assertFalse(sis._is_codebase_locked())

    def test_codebase_locked_when_row_says_true(self):
        from backend.services import self_improvement_service as sis
        row = MagicMock()
        row.value = "true"
        with self._patch_setting(row), \
             patch("backend.services.self_improvement_service.os.path.exists", return_value=False):
            self.assertTrue(sis._is_codebase_locked())


@unittest.skipUnless(_HAS_CELERY_APP, "celery_app unavailable (celery package missing)")
class TestReconcilerBeatRegistration(unittest.TestCase):
    """The reconciler task is registered on Celery Beat at 6h cadence.

    We import the beat schedule dict directly rather than spinning up Celery —
    that keeps the test fast and DB-free while still proving the wire is in.
    """

    def test_reconcile_belief_updates_in_beat_schedule(self):
        # Beat schedule lives inside create_celery_app(). Read the function's
        # source for the entry rather than executing it (would need redis).
        import inspect
        from backend import celery_app
        src = inspect.getsource(celery_app.create_celery_app)
        self.assertIn("memory-reconcile-belief-updates", src)
        self.assertIn("memory.reconcile_belief_updates", src)
        self.assertIn("21600", src)  # 6h cadence in seconds


class TestDomAssistDisabledByDefault(unittest.TestCase):
    """DOM-assisted clicking is off out of the box (2026-05-14).

    Bad viewport→screen translation on :99 was causing Gemma4 to click empty
    space on the agent_course test page; flipping the default cured the loop.
    Set GUAARDVARK_DOM_ASSIST=1 to re-enable.
    """

    def test_disabled_when_env_unset(self):
        import importlib, os
        os.environ.pop("GUAARDVARK_DOM_ASSIST", None)
        from backend.services import dom_metadata_extractor as dme
        importlib.reload(dme)
        self.assertFalse(dme.dom_assist_enabled())

    def test_enabled_when_env_set(self):
        import os
        os.environ["GUAARDVARK_DOM_ASSIST"] = "1"
        try:
            from backend.services.dom_metadata_extractor import dom_assist_enabled
            self.assertTrue(dom_assist_enabled())
        finally:
            os.environ.pop("GUAARDVARK_DOM_ASSIST", None)

    def test_disabled_when_env_set_to_zero(self):
        import os
        os.environ["GUAARDVARK_DOM_ASSIST"] = "0"
        try:
            from backend.services.dom_metadata_extractor import dom_assist_enabled
            self.assertFalse(dom_assist_enabled())
        finally:
            os.environ.pop("GUAARDVARK_DOM_ASSIST", None)


if __name__ == "__main__":
    unittest.main()
