#!/usr/bin/env python3
"""DOM-based verification fast-path — confirm cheaply, fall through to vision.

Locks in that _dom_confirms_target only short-circuits SUCCESS on a real match
(never a guess), and that _wait_until_visible uses it before the expensive vision
poll — the fix for the 60s task-budget blowouts.
"""
import os
import sys
import types
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"


def _bare_service():
    from backend.services.agent_control_service import AgentControlService
    return AgentControlService.__new__(AgentControlService)


def _el(text="", element_type=""):
    return types.SimpleNamespace(text=text, element_type=element_type)


def _snap(title="", url="", elements=(), success=True):
    return types.SimpleNamespace(title=title, url=url, elements=list(elements), success=success)


def _patch_dom(snap, grounding=True):
    """Patch the extractor + grounding flag that _dom_confirms_target imports."""
    ext = types.SimpleNamespace()
    ext.get_instance = lambda: types.SimpleNamespace(extract=lambda: snap)
    return patch.dict("sys.modules", {}), patch.multiple(
        "backend.services.dom_metadata_extractor",
        DOMMetadataExtractor=ext,
        dom_grounding_enabled=lambda: grounding,
    )


class TestDomConfirmsTarget:
    def test_url_match_is_solid_nav_confirm(self):
        svc = _bare_service()
        snap = _snap(title="guaardvark - YouTube", url="https://www.youtube.com/results?q=guaardvark")
        _, p = _patch_dom(snap)
        with p:
            assert svc._dom_confirms_target("youtube search results visible") is True

    def test_distinctive_content_match(self):
        svc = _bare_service()
        snap = _snap(title="Search", url="https://x.example",
                     elements=[_el(text="Watch how I make Batman Videos with guaardvark")])
        _, p = _patch_dom(snap)
        with p:
            assert svc._dom_confirms_target("guaardvark video results") is True

    def test_generic_words_alone_do_not_confirm(self):
        svc = _bare_service()
        snap = _snap(title="Some Page", url="https://x.example",
                     elements=[_el(text="Submit", element_type="button")])
        _, p = _patch_dom(snap)
        with p:
            # only stopwords → no distinctive tokens → cannot confirm
            assert svc._dom_confirms_target("the submit button is now visible") is False

    def test_no_match_falls_through(self):
        svc = _bare_service()
        snap = _snap(title="Cat Videos", url="https://cats.example",
                     elements=[_el(text="fluffy kittens")])
        _, p = _patch_dom(snap)
        with p:
            assert svc._dom_confirms_target("memory updated successfully") is False

    def test_grounding_off_returns_false(self):
        svc = _bare_service()
        snap = _snap(url="https://www.youtube.com/x")
        _, p = _patch_dom(snap, grounding=False)
        with p:
            assert svc._dom_confirms_target("youtube") is False

    def test_failed_snapshot_returns_false(self):
        svc = _bare_service()
        _, p = _patch_dom(_snap(url="https://youtube.com", success=False))
        with p:
            assert svc._dom_confirms_target("youtube") is False


class TestWaitUntilVisibleFastPath:
    def test_dom_confirm_short_circuits_before_vision(self):
        svc = _bare_service()
        with patch.object(svc, "_dom_confirms_target", return_value=True):
            # No screen/vision needed — DOM confirms on poll 1.
            res = svc._wait_until_visible("guaardvark results", screen=None, timeout_s=5.0)
        assert res["success"] is True
        assert res["via"] == "dom"
        assert res["polls"] == 1

    def test_fastpath_disabled_skips_dom(self):
        svc = _bare_service()
        # DOM would confirm, but allow_dom_fastpath=False must ignore it and go to
        # vision. With a raising screen + tiny timeout, it fails (never via=dom).
        boom_screen = types.SimpleNamespace(capture=lambda: (_ for _ in ()).throw(RuntimeError("x")))
        fake_va = types.SimpleNamespace(VisionAnalyzer=lambda: types.SimpleNamespace(default_model="m"))
        with patch.object(svc, "_dom_confirms_target", return_value=True), \
             patch.dict("sys.modules", {"backend.utils.vision_analyzer": fake_va}):
            res = svc._wait_until_visible(
                "guaardvark", screen=boom_screen, timeout_s=0.2,
                poll_interval_s=0.05, allow_dom_fastpath=False,
            )
        assert res["success"] is False
        assert res.get("via") != "dom"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
