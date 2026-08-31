"""Shared NL control plane primitives: normalize + dispatch (no LLM)."""
import pytest

from backend.services import nl_control_plane as ncp

VALID = {"status", "scout_and_draft", "refuse"}


class TestNormalize:
    def test_unknown_intent_falls_back_to_default(self):
        out = ncp.normalize({"intent": "nonsense"}, valid_intents=VALID)
        assert out["intent"] == "refuse"

    def test_valid_intent_and_confidence_clamp(self):
        out = ncp.normalize(
            {"intent": "SCOUT_AND_DRAFT", "confidence": 1.7}, valid_intents=VALID
        )
        assert out["intent"] == "scout_and_draft"  # lowercased
        assert out["confidence"] == 1.0  # clamped

    def test_bad_confidence_and_int_coercion(self):
        out = ncp.normalize(
            {"intent": "status", "confidence": "oops", "draft_id": "42"},
            valid_intents=VALID,
            int_fields=("draft_id",),
        )
        assert out["confidence"] == 0.0
        assert out["draft_id"] == 42

    def test_non_numeric_int_field_becomes_none(self):
        out = ncp.normalize(
            {"intent": "status", "draft_id": "abc"},
            valid_intents=VALID,
            int_fields=("draft_id",),
        )
        assert out["draft_id"] is None

    def test_does_not_mutate_input(self):
        raw = {"intent": "junk", "confidence": 5}
        ncp.normalize(raw, valid_intents=VALID)
        assert raw == {"intent": "junk", "confidence": 5}


class TestDispatch:
    def test_routes_to_handler(self):
        out = ncp.dispatch(
            {"intent": "status"},
            {"status": lambda c: "S"},
            default_handler=lambda c: "DEFAULT",
        )
        assert out == "S"

    def test_unknown_intent_hits_default(self):
        out = ncp.dispatch(
            {"intent": "scout_and_draft"},
            {"status": lambda c: "S"},
            default_handler=lambda c: "DEFAULT",
        )
        assert out == "DEFAULT"

    def test_missing_intent_key_hits_default(self):
        out = ncp.dispatch(
            {}, {"status": lambda c: "S"}, default_handler=lambda c: "DEFAULT"
        )
        assert out == "DEFAULT"
