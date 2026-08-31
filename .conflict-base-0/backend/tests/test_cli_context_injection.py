import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

try:
    from backend.services.unified_chat_engine import UnifiedChatEngine
except Exception:
    pytest.skip("Unified chat engine dependencies not available", allow_module_level=True)


def test_interface_context_is_formatted_for_prompt_injection():
    engine = UnifiedChatEngine(tool_registry=None, llm_instance=None)

    context = engine._format_interface_context({
        "context": "[CLI Working Context]\nActive file: /tmp/test-container.sh"
    })

    assert "Interface-provided context:" in context
    assert "Active file: /tmp/test-container.sh" in context


def test_interface_context_ignores_blank_or_non_string_values():
    engine = UnifiedChatEngine(tool_registry=None, llm_instance=None)

    assert engine._format_interface_context({"context": "   "}) == ""
    assert engine._format_interface_context({"context": {"bad": "shape"}}) == ""
