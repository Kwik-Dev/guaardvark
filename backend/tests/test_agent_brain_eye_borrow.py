"""Seeing is not pointing: a sighted model that misses keeps deciding and a
measured pointer does the looking (resolve_brain_eye)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("GUAARDVARK_MODE", "test")

from backend.services.agent_control_service import AgentControlService as A
from backend.services import model_capability_resolver as R


class _Profile:
    sees_natively = True
    can_drive_screen = True


class BrainEyeBorrowTest(unittest.TestCase):
    def _resolve(self, lend):
        with patch.object(A, "_get_unified_model", staticmethod(lambda active="", screen=None: active)), \
             patch.object(R, "better_eye_for", return_value=lend), \
             patch.object(R, "resolve", return_value=_Profile()):
            return A.resolve_brain_eye(active="gemma4:e2b", screen_size=(1000, 1000))

    def test_a_poor_pointer_keeps_deciding_and_a_good_one_looks(self):
        be = self._resolve({"tag": "gemma4:12b", "eye_px": 5.8, "own_px": 255.0})
        self.assertEqual((be.brain, be.eye, be.unified, be.eye_mechanism),
                         ("gemma4:e2b", "gemma4:12b", False, "sibling_vlm"))
        self.assertIn("255px", be.reason)

    def test_without_a_better_eye_the_model_looks_for_itself(self):
        be = self._resolve(None)
        self.assertEqual((be.eye, be.unified, be.eye_mechanism), ("gemma4:e2b", True, "native"))


if __name__ == "__main__":
    unittest.main()
