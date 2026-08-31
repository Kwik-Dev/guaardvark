"""Regression tests for prompt_enhancer.has_text_intent.

False positives (e.g. scare-quotes like 'crow') previously re-routed batch
photoreal jobs off Z-Image onto SDXL with turbo sampling and skipped Realistic
style enhancement — producing artwork instead of photos.
"""

import importlib.util
from pathlib import Path

import pytest

_PE_PATH = Path(__file__).resolve().parents[1] / "utils" / "prompt_enhancer.py"
_spec = importlib.util.spec_from_file_location("prompt_enhancer_standalone", _PE_PATH)
_pe = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_pe)
has_text_intent = _pe.has_text_intent


@pytest.mark.parametrize(
    "prompt,expected",
    [
        # Real on-image text
        ('a sign that reads "OPEN"', True),
        ("logo with 'HULK' on chest", True),
        ("sign saying 'OPEN 24 HOURS'", True),
        ("company logo on the wall", True),
        ("title “BATMAN” on poster", True),
        # Scare-quotes / ordinary prose — must NOT trigger
        ("no pointy 'crow' styling", False),
        ("the 'best' day ever", False),
        ("the joker, Realistic, Cinema", False),
        (
            "the joker, tall, skinny, purple pinstripe suit, "
            "no pointy 'crow' styling. Brick wall, night time. Realistic.",
            False,
        ),
        ("", False),
    ],
)
def test_has_text_intent(prompt, expected):
    assert has_text_intent(prompt) is expected
