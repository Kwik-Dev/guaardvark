#!/usr/bin/env python3
"""Live validation of the native Ollama function-calling path (Lever 1, obs #1032).

Proves the previously-unvalidated claims against REAL models before we flip the
default on:
  1. A tools-capable model (qwen2.5:14b), handed our as_ollama_tools schema,
     emits STRUCTURED message.tool_calls that _native_tool_calls_to_response
     converts cleanly.
  2. model_supports_tools() correctly gates: True for the capable model, False
     for a completion-only model (llama3) -> that model would take the XML path.

Run: backend/venv/bin/python scripts/validate_native_toolcalls.py
Requires a running Ollama with qwen2.5:14b + llama3 pulled.
"""
import json
import sys
import requests

OLLAMA = "http://127.0.0.1:11434"
CAPABLE = "qwen2.5:14b"      # [completion, tools]
NONTOOLS = "llama3:latest"   # [completion] only

from backend.services.agent_tools import ToolRegistry, BaseTool, ToolParameter, ToolResult
from backend.utils.ollama_resource_manager import model_supports_tools
from backend.services.unified_chat_engine import UnifiedChatEngine


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Search the web for current information. Use for facts you don't know."
    parameters = {
        "query": ToolParameter(name="query", type="string", required=True,
                               description="The search query"),
    }
    def execute(self, **kwargs):
        return ToolResult(success=True, output="(stub)")


def ok(b):
    return "\033[92mPASS\033[0m" if b else "\033[91mFAIL\033[0m"


def main():
    results = []
    reg = ToolRegistry()
    reg.register(WebSearchTool())
    schema = reg.as_ollama_tools()
    print(f"as_ollama_tools schema:\n{json.dumps(schema, indent=2)}\n")

    # ── 1. capability gate (live /api/show) ──
    cap = model_supports_tools(CAPABLE)
    nontools = model_supports_tools(NONTOOLS)
    print(f"[{ok(cap)}] model_supports_tools({CAPABLE}) = {cap}  (expect True)")
    print(f"[{ok(not nontools)}] model_supports_tools({NONTOOLS}) = {nontools}  (expect False -> XML path)")
    results += [cap, not nontools]

    # ── 2. capable model emits structured tool_calls for our schema ──
    payload = {
        "model": CAPABLE,
        "messages": [{"role": "user", "content": "What is the latest news about the James Webb telescope? Use a tool."}],
        "tools": schema,
        "stream": False,
    }
    print(f"\nCalling {CAPABLE} with native tools schema (first load may take ~30s)...")
    try:
        r = requests.post(f"{OLLAMA}/api/chat", json=payload, timeout=180)
        r.raise_for_status()
        msg = r.json().get("message", {})
        native_calls = msg.get("tool_calls") or []
        print(f"raw message.tool_calls: {json.dumps(native_calls, indent=2)}")
        got_structured = len(native_calls) > 0 and native_calls[0].get("function", {}).get("name") == "web_search"
        print(f"[{ok(got_structured)}] {CAPABLE} emitted a structured web_search call")
        results.append(got_structured)

        # ── 3. our converter turns it into a usable ToolCallResponse ──
        eng = UnifiedChatEngine.__new__(UnifiedChatEngine)
        resp = eng._native_tool_calls_to_response(native_calls, msg.get("content", ""))
        conv_ok = (len(resp.tool_calls) == 1
                   and resp.tool_calls[0].tool_name == "web_search"
                   and "query" in resp.tool_calls[0].parameters)
        print(f"[{ok(conv_ok)}] _native_tool_calls_to_response -> "
              f"tool={resp.tool_calls[0].tool_name if resp.tool_calls else None} "
              f"params={resp.tool_calls[0].parameters if resp.tool_calls else None}")
        results.append(conv_ok)
    except Exception as e:
        print(f"[{ok(False)}] live call failed: {e}")
        results.append(False)

    print(f"\n{'='*50}\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
