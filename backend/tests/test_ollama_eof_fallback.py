"""Tests for Ollama EOF classification and user messaging."""
import pytest
from unittest.mock import patch, MagicMock


class TestClassifyOllamaEofKind:
    def test_service_down_on_connection_refused(self):
        from backend.services.unified_chat_engine import classify_ollama_eof_kind
        assert classify_ollama_eof_kind("llama3", "Connection refused") == "service_down"

    @patch("backend.services.unified_chat_engine._ollama_chat_model_loaded", return_value=True)
    def test_parser_eof_qwen35_loaded(self, _mock_ps):
        from backend.services.unified_chat_engine import classify_ollama_eof_kind
        kind = classify_ollama_eof_kind(
            "jaahas/qwen3.5-uncensored:latest",
            "EOF (status code: -1)",
        )
        assert kind == "parser_eof"

    @patch("backend.services.unified_chat_engine._ollama_chat_model_loaded", return_value=False)
    def test_runner_eof_when_model_not_loaded(self, _mock_ps):
        from backend.services.unified_chat_engine import classify_ollama_eof_kind
        kind = classify_ollama_eof_kind("llama3:latest", "EOF (status code: -1)")
        assert kind == "runner_eof"

    @patch("backend.services.unified_chat_engine._ollama_chat_model_loaded", return_value=True)
    def test_non_qwen_loaded_defaults_runner(self, _mock_ps):
        from backend.services.unified_chat_engine import classify_ollama_eof_kind
        kind = classify_ollama_eof_kind("gemma4:e4b", "EOF (status code: -1)")
        assert kind == "runner_eof"


class TestOllamaEofUserMessage:
    @patch("backend.services.unified_chat_engine.classify_ollama_eof_kind", return_value="parser_eof")
    def test_parser_specific_message(self, _mock_kind):
        from backend.services.unified_chat_engine import ollama_eof_user_message
        msg = ollama_eof_user_message("EOF", "jaahas/qwen3.5-uncensored:latest")
        assert "tool-call parser" in msg
        assert "VRAM" not in msg

    @patch("backend.services.unified_chat_engine.classify_ollama_eof_kind", return_value="runner_eof")
    def test_runner_message_mentions_loading(self, _mock_kind):
        from backend.services.unified_chat_engine import ollama_eof_user_message
        msg = ollama_eof_user_message("EOF", "llama3")
        assert "loading" in msg.lower() or "swapped" in msg.lower()


class TestEofNonStreamRetryIntegration:
    """Verify _call_llm_streaming EOF path attempts non-stream retry."""

    @patch("backend.services.unified_chat_engine.is_aborted", return_value=False)
    @patch("backend.services.unified_chat_engine.classify_ollama_eof_kind", return_value="parser_eof")
    @patch("backend.services.unified_chat_engine._ollama_chat_model_loaded", return_value=True)
    def test_eof_falls_back_to_non_stream(self, _ps, _kind, _abort):
        import backend.services.unified_chat_engine as uce

        stream_err = uce.ResponseError if hasattr(uce, "ResponseError") else Exception
        try:
            from ollama import ResponseError as OllamaResponseError
        except ImportError:
            OllamaResponseError = Exception

        call_log = []

        def fake_chat(**kwargs):
            call_log.append(dict(kwargs))
            if kwargs.get("stream", True):
                raise OllamaResponseError("EOF (status code: -1)")
            return {
                "message": {"content": "Recovered via non-stream"},
                "prompt_eval_count": 10,
                "eval_count": 5,
            }

        engine = uce.UnifiedChatEngine.__new__(uce.UnifiedChatEngine)
        engine.llm = MagicMock(model="jaahas/qwen3.5-uncensored:latest", context_window=8192)
        engine._native_toolcalls_active = False
        engine._think = False
        engine._native_pending_tool_calls = None

        with patch("ollama.chat", fake_chat):
            text, in_tok, out_tok = engine._call_llm_streaming(
                [{"role": "user", "content": "hi"}],
                lambda *a, **k: None,
                "sess-1",
                emit_tokens=False,
            )

        assert text == "Recovered via non-stream"
        assert any(c.get("stream") is False for c in call_log)
