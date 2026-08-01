#!/usr/bin/env python3
"""Phase 1 — Ground the Eye: DOM element inventory reaches the decision prompt.

The agent's #1 failure mode was the vision model hallucinating what's in a text
field instead of reading the page. These tests lock in that the real element
inventory (label + role + focused-state, coordinate-free) is formatted and
injected into the decision prompt when grounding is enabled.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services.dom_metadata_extractor import (  # noqa: E402
    DOMMetadataExtractor,
    DOMSnapshot,
    ElementInfo,
    dom_grounding_enabled,
)


def _sample_snapshot() -> DOMSnapshot:
    return DOMSnapshot(
        url="https://reddit.com/r/LocalLLaMA/comments/abc",
        title="LocalLLaMA thread",
        success=True,
        elements=[
            ElementInfo(
                tag="textarea", text="Add a comment", element_type="composer",
                x=100, y=400, w=600, h=80, cx=400, cy=440, focused=True,
            ),
            ElementInfo(
                tag="button", text="Comment", element_type="button",
                x=620, y=500, w=90, h=30, cx=665, cy=515,
            ),
        ],
    )


class TestFormatForPrompt(unittest.TestCase):
    def test_grounding_variant_lists_labels_and_focused_without_coords(self):
        out = DOMMetadataExtractor.format_for_prompt(_sample_snapshot(), include_coords=False)
        # Real labels the brain should reuse for target_description
        self.assertIn('"Add a comment"', out)
        self.assertIn('"Comment"', out)
        # Focused-state is the signal that lets the brain type directly
        self.assertIn("(focused)", out)
        # Coordinate-free: the unreliable (cx,cy) pixels must NOT appear
        self.assertNotIn("(400,440)", out)
        self.assertNotIn("at (", out)

    def test_coords_variant_still_includes_pixels(self):
        out = DOMMetadataExtractor.format_for_prompt(_sample_snapshot(), include_coords=True)
        self.assertIn("(400,440)", out)

    def test_empty_snapshot_returns_empty(self):
        self.assertEqual(DOMMetadataExtractor.format_for_prompt(DOMSnapshot()), "")


class TestGroundingFlag(unittest.TestCase):
    def test_default_on(self):
        os.environ.pop("GUAARDVARK_DOM_GROUNDING", None)
        self.assertTrue(dom_grounding_enabled())

    def test_explicit_off(self):
        os.environ["GUAARDVARK_DOM_GROUNDING"] = "0"
        try:
            self.assertFalse(dom_grounding_enabled())
        finally:
            os.environ.pop("GUAARDVARK_DOM_GROUNDING", None)


class TestServiceInjection(unittest.TestCase):
    """_format_dom_grounding_for_prompt on a bare instance (skip heavy __init__)."""

    def _bare_service(self):
        from backend.services.agent_control_service import AgentControlService
        svc = AgentControlService.__new__(AgentControlService)
        svc._dom_snapshot = None
        return svc

    def test_no_snapshot_yields_empty(self):
        svc = self._bare_service()
        self.assertEqual(svc._format_dom_grounding_for_prompt(), "")

    def test_snapshot_present_yields_inventory(self):
        os.environ.pop("GUAARDVARK_DOM_GROUNDING", None)
        svc = self._bare_service()
        svc._dom_snapshot = _sample_snapshot()
        block = svc._format_dom_grounding_for_prompt()
        self.assertIn('"Add a comment"', block)
        self.assertIn("(focused)", block)
        self.assertNotIn("at (", block)  # coordinate-free
        self.assertTrue(block.endswith("\n\n"))

    def test_grounding_off_suppresses_inventory(self):
        os.environ["GUAARDVARK_DOM_GROUNDING"] = "0"
        try:
            svc = self._bare_service()
            svc._dom_snapshot = _sample_snapshot()
            self.assertEqual(svc._format_dom_grounding_for_prompt(), "")
        finally:
            os.environ.pop("GUAARDVARK_DOM_GROUNDING", None)


if __name__ == "__main__":
    unittest.main()
