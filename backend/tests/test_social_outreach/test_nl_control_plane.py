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


class _StubClient:
    """Stands in for ollama.Client: records how it was built and what it was asked."""

    built: list = []

    def __init__(self, host=None, timeout=None):
        self.host, self.timeout, self.calls = host, timeout, []
        _StubClient.built.append(self)

    def chat(self, **kw):
        self.calls.append(kw)
        return {"message": {"content": '{"answer": true}'}}


@pytest.fixture
def stub_ollama(monkeypatch):
    import ollama
    from backend.utils import ollama_resource_manager as orm

    module_calls = []

    def module_chat(**kw):
        module_calls.append(kw)
        return {"message": {"content": '{"answer": false}'}}

    _StubClient.built = []
    monkeypatch.setattr(ollama, "Client", _StubClient)
    monkeypatch.setattr(ollama, "chat", module_chat)
    monkeypatch.setattr(ncp, "_clients", {})
    monkeypatch.setattr(orm, "get_model_info", lambda *a, **k: None)
    monkeypatch.setattr(orm, "compute_optimal_num_ctx", lambda model: 8192)
    return module_calls


class TestJsonChatOptions:
    def test_no_timeout_keeps_the_module_level_call(self, stub_ollama):
        out = ncp.json_chat("sys", "usr", model="m:1")
        assert out == {"answer": False}
        assert _StubClient.built == []
        assert stub_ollama[0]["options"]["num_ctx"] == 8192

    def test_timeout_builds_a_client_with_that_timeout(self, stub_ollama):
        out = ncp.json_chat("sys", "usr", model="m:1", timeout=1.5)
        assert out == {"answer": True}
        assert stub_ollama == []
        (client,) = _StubClient.built
        assert client.timeout == 1.5
        assert client.calls[0]["format"] == "json"

    def test_num_ctx_is_passed_through(self, stub_ollama):
        ncp.json_chat("sys", "usr", model="m:1", timeout=2.0, num_ctx=32768)
        assert _StubClient.built[0].calls[0]["options"]["num_ctx"] == 32768

    def test_one_client_per_timeout(self, stub_ollama):
        ncp.json_chat("sys", "usr", model="m:1", timeout=2.0)
        ncp.json_chat("sys", "usr", model="m:1", timeout=2.0)
        assert len(_StubClient.built) == 1


class TestJsonChatResult:
    def _with_chat(self, monkeypatch, chat):
        import ollama
        from backend.utils import ollama_resource_manager as orm

        monkeypatch.setattr(ollama, "chat", chat)
        monkeypatch.setattr(orm, "get_model_info", lambda *a, **k: None)
        monkeypatch.setattr(orm, "compute_optimal_num_ctx", lambda model: 8192)

    def test_a_timeout_is_reported_as_timeout(self, monkeypatch):
        import httpx

        def chat(**kw):
            raise httpx.ReadTimeout("timed out")

        self._with_chat(monkeypatch, chat)
        result = ncp.json_chat_result("s", "u", model="m:1")
        assert result.failure == "timeout"
        assert ncp.json_chat("s", "u", model="m:1", on_error={"x": 1}) == {"x": 1}

    def test_a_connection_error_is_reported_as_error(self, monkeypatch):
        def chat(**kw):
            raise ConnectionError("refused")

        self._with_chat(monkeypatch, chat)
        assert ncp.json_chat_result("s", "u", model="m:1").failure == "error"

    def test_a_reply_without_json_is_a_parse_failure(self, monkeypatch):
        self._with_chat(monkeypatch, lambda **kw: {"message": {"content": "sure thing"}})
        result = ncp.json_chat_result("s", "u", model="m:1")
        assert result.failure == "parse_fail"
        assert result.raw == "sure thing"

    def test_a_wrapped_reply_is_read_from_its_braces(self, monkeypatch):
        self._with_chat(monkeypatch, lambda **kw: {"message": {"content": 'Here: {"a": 1} done'}})
        result = ncp.json_chat_result("s", "u", model="m:1")
        assert result.failure is None and result.data == {"a": 1}
