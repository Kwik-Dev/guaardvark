"""The unified prompt must not change when its blocks are shared with split mode."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.tests._gen_unified_golden import build  # noqa: E402

GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "unified_prompt_golden.txt")


def test_unified_prompt_is_byte_identical_to_the_golden():
    with open(GOLDEN) as f:
        expected = f.read()
    got = build()
    assert got == expected, "the unified prompt changed; if intentional, regenerate the golden deliberately"
