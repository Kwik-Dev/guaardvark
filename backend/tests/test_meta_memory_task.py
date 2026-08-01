"""Meta/memory task routing — off the screen loop, no visual-proof spiral."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"


def _bare_service():
    from backend.services.agent_control_service import AgentControlService
    return AgentControlService.__new__(AgentControlService)


class TestMetaMemoryRouting:
    def test_save_learnings_routes_off_screen(self):
        svc = _bare_service()
        with patch.object(svc, "_write_session_lessons", return_value=3) as w:
            res = svc._try_meta_memory_task("Good work, save your learnings to memory please")
        assert res is not None and res.success is True
        assert "3 new lesson" in res.reason
        assert res.verified is True  # no done-proof spiral possible
        w.assert_called_once()

    def test_remember_this_routes_off_screen(self):
        svc = _bare_service()
        with patch.object(svc, "_write_session_lessons", return_value=0):
            res = svc._try_meta_memory_task("remember this for next time")
        assert res is not None and res.success is True

    def test_screen_tasks_stay_with_loop(self):
        svc = _bare_service()
        # memory words + a screen verb → NOT meta; the loop must handle it
        assert svc._try_meta_memory_task("click save in the memory settings page") is None
        assert svc._try_meta_memory_task("go to youtube and search for guaardvark") is None
        assert svc._try_meta_memory_task(
            "update your knowledge by reading the browser page") is None

    def test_lesson_flush_failure_is_nonfatal(self):
        svc = _bare_service()
        with patch.object(svc, "_write_session_lessons", side_effect=RuntimeError("db down")):
            res = svc._try_meta_memory_task("save your learnings")
        assert res is not None and res.success is True
        assert "0 new lesson" in res.reason

    def test_empty_and_long_tasks_pass_through(self):
        svc = _bare_service()
        assert svc._try_meta_memory_task("") is None
        assert svc._try_meta_memory_task("save learnings " + "x" * 200) is None
