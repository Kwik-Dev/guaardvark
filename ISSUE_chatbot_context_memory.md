# [OPEN] Chatbot is less capable than expected — context window, memory recall, and compaction limits

- **Status:** Open — no fix applied. Analysis only.
- **Area:** `backend/services/unified_chat_engine.py`, `backend/services/brain_state.py`, `backend/services/agent_brain.py`, `backend/api/memory_api.py`, `backend/config.py`.

## Symptom

Compared to a large-context agent (e.g. pi), Guaardvark chat feels "dumber": it loses
thread across a conversation, forgets relevant durable memories, and has little working
context per turn. The architecture (three-tier AgentBrain routing, typed/scoped/audited
memory, rolling session summaries, context-aware tool selection) is sound — the gap comes
from the practical constraints below.

## Root cause

The chat runs **local Ollama models with an 8192-token context window** and
**keyword-based memory recall**, and several config/implementation choices make the
effective context even smaller than it needs to be. (Note: the runtime can also route
chat to a cloud provider — see §8 — but the context/compaction/memory limits below
apply regardless of provider.)

### 1. Tiny context window + aggressive, lossy compaction
- `unified_chat_engine.py:1507` hardcodes `context_window=8192`.
- `COMPACTION_THRESHOLD = 0.7` (`config.py:181`) → compacts once history exceeds ~5,700 tokens.
- `_compact_history` (`unified_chat_engine.py:3477`) keeps only the **last 8 messages** and
  summarizes everything older into **200 words using the same small local model**
  (`ollama_client.chat`, line 3500). Very lossy; the model effectively only sees ~8 recent
  turns + a 200-word summary.

### 2. History is loaded small, then compacted again
- `AGENTIC_HISTORY_LIMIT = 30` messages (`config.py:687`).
- `CHAT_HISTORY_MAX_TOKENS_FOR_ENGINE = 3072` tokens (`config.py:692`).
- So only ~3K tokens of history are allowed even before compaction.

### 3. Memory recall is keyword-based, not semantic
- `_query_memories` (`memory_api.py:534`) filters with `ilike` substring matching on
  content/tags. The comment explicitly says *"Embeddings can layer on later."*
- A memory about "the client's brand voice" won't surface for a query about "tone of our
  marketing copy" unless the words literally match. No vector/semantic recall.

### 4. Memory injection budget is tiny
- `brain_state.py:512`: `get_memories_for_context(max_tokens=500)`.
- `agent_brain.py:383`: `max_tokens=300` on escalation.
- Only ~300–500 tokens of durable memory reach the prompt, even though
  `CHAT_MEMORY_TOKEN_LIMIT = 4096` exists in config and is never used.

### 5. System prompt bloat eats the small window
- The prompt assembles up to **25 tools** (~20 tokens each ≈ 500 tokens) + MCP inventory +
  memory block + desktop state + budget + facts + rules. With an 8192 window this leaves
  little room for actual conversation.

### 6. Tool selection can silently drop the right tool
- `select_tools_for_context` caps at 25 tools; the semantic selector can return
  **CORE-only when embeddings are cold** (`unified_chat_engine.py:787`), so the model may
  not have the tool it needs available.

### 7. No subagents / parallel reasoning in chat
- Chat is a single-threaded ReACT loop (Tier 3 Deliberation, 3–10 calls,
  `TOTAL_STEP_CAP = 20`). No parallel decomposition.

### 8. LLM call path & cloud routing — two divergent code paths, no native tool-calling
- **Tier 2** (`UnifiedChatEngine._call_llm_streaming`, `unified_chat_engine.py:3025`)
  computes a local `_use_cloud` flag from `llm_provider.get_active_provider()` and
  dispatches: local → `ollama.chat()` (`/api/chat`); cloud → `mistral_provider.chat()`
  / `openai_provider.chat()` which POST directly to the vendor's `/chat/completions`
  (they only *mimic* `ollama.chat`'s chunk shape — they do **not** route through Ollama).
- **Tier 3** (`AgentExecutor`, `agent_executor.py`) is a **separate** ReACT loop that
  never reads `_use_cloud`. It uses `self.llm` (LlamaIndex LLM from `BrainState`,
  built by `get_default_llm()`), which routes to an OpenAI-compatible LLM when
  `GUAARDVARK_OPENAI_BASE_URL` + `GUAARDVARK_OPENAI_MODEL` are set. So Tier 2 and Tier 3
  hit the cloud through **two different code paths** with different tool-call mechanisms.
- **Tool calling is text-parsing, never native, on the cloud path.** Native structured
  tool-calling (`tools=[...]` schema) is gated on `not _use_cloud`
  (`unified_chat_engine.py:3145`) and on `model_supports_tools()` (an Ollama check).
  With a cloud provider active: Tier 2 uses **XML-in-prompt** (`parse_tool_calls_xml`),
  Tier 3 uses **JSON-structured-output** (`parse_tool_calls_structured`). Both are
  regex/parse heuristics — less reliable than native function calling.
- **Current runtime state (queried from settings DB):** `cloud_models_enabled=true`,
  `llm_provider=openai`, `openai_available=true`, `cloud_active=true`,
  `active_cloud_model=deepseek-v4-flash:0731`. So `_use_cloud` is **True** today — chat
  is routed to the OpenAI-compatible endpoint, and tool-calling rides the text-parse paths.

## Proposed work / steps (highest leverage first)

1. **Raise the context window** — use the `compute_optimal_num_ctx(model_name)` result
   (`unified_chat_engine.py:3106`) instead of the hardcoded 8192, and raise
   `COMPACTION_THRESHOLD` / keep more recent messages (8 → 20).
2. **Semantic memory recall** — add embedding-based retrieval to `_query_memories`
   (embedding infra already exists in the tool selector, `SemanticToolSelector`).
3. **Use the real memory budget** — bump `max_tokens` from 500 to the configured
   `CHAT_MEMORY_TOKEN_LIMIT` (4096).
4. **Better compaction** — summarize with a stronger model, or keep more recent messages.
5. **Native tool-calling for cloud** — extend the native `tools=[...]` path to cloud
   providers (currently gated on `not _use_cloud`), or at least unify Tier 2/Tier 3 on
   one reliable tool-call mechanism instead of XML vs JSON heuristics.

## Handout: Option B — pi RPC mode bridge (use pi's full agent as Guaardvark's brain)

> **Goal:** make pi-the-agent (not just pi's model gateway) the actual brain behind
> Guaardvark chat — with pi's own skills, subagents, session management, compaction,
> and thinking levels. This is the only option that truly *substitutes* pi's agent for
> Guaardvark's brain; when it's active, Guaardvark's memory/compaction/tool-selection
> layers become mostly irrelevant (pi does all of that itself).

### Why RPC mode

pi ships a native headless protocol: `pi --mode rpc` — JSONL over stdin/stdout. It gives
full agent control (`prompt`, `steer`, `follow_up`, `abort`, `new_session`, `get_state`,
`get_messages`, `set_model`, `set_thinking_level`, `compact`, `bash`, session
management) and streams rich events (`message_update` with `text_delta` / `thinking_delta`
/ `toolcall_delta`, `tool_execution_*`, `compaction_*`). This is the *actual* pi brain.

### Prerequisites

- `pi` installed and at least one provider logged in (`pi /login`).
- A provider key configured (e.g. `ANTHROPIC_API_KEY`).
- Python 3.12 (Guaardvark's constraint).

### Architecture

```
Guaardvark chat (Tier 2/3)
   └─ pi_rpc_provider (new Python module)
        └─ subprocess: pi --mode rpc   (one long-lived process per session)
             ├─ stdin:  JSONL commands  (prompt, steer, ...)
             └─ stdout: JSONL events    (message_update, tool_execution_*, ...)
```

Two integration points in Guaardvark (pick one):

1. **As a new provider** — add `pi` to `CLOUD_PROVIDERS` in `llm_provider.py` + a client
   module mirroring `mistral_provider.py` / `openai_provider.py` (`chat()` yielding
   Ollama-shaped chunks, `make_llamaindex_llm()` returning a LlamaIndex `CustomLLM`).
   Both Tier 2 (`_call_llm_streaming`) and Tier 3 (`self.llm`) pick it up. **Code change.**
2. **As a service plugin** — a `plugins/pi_agent/` with a `plugin.json` (type: service)
   that runs the RPC bridge on a port, health-checked by the plugin manager. Closest to
   the existing plugin pattern. **More work, but managed.**

### RPC protocol essentials (from `packages/coding-agent/docs/rpc.md`)

- **Framing:** strict JSONL, `\n` only. Split on `\n`; strip a trailing `\r`. Do **not**
  use `readline` (it also splits on `U+2028`/`U+2029`).
- **Commands** (stdin): `{"type":"prompt","message":"..."}`,
  `{"type":"steer","message":"..."}`, `{"type":"abort"}`,
  `{"type":"new_session"}`, `{"type":"get_state"}`, `{"type":"compact"}`,
  `{"type":"set_thinking_level","level":"high"}`.
- **Responses** (stdout): `{"type":"response","command":"prompt","success":true}`.
  `success:true` = accepted; failures after acceptance come through the event stream.
- **Events** (stdout): `message_update` carries `assistantMessageEvent` with
  `text_delta` / `thinking_delta` / `toolcall_delta`; `tool_execution_*` stream tool
  progress; `agent_settled` = run fully done (no retry/compaction/queued continuation).
- **Streaming during a prompt:** if the agent is already streaming, a new `prompt` needs
  `streamingBehavior` (`"steer"` or `"followUp"`), else it errors.

### Python bridge sketch (`backend/services/pi_rpc_provider.py`)

```python
import json, subprocess, threading
from typing import Iterator, Dict, Any

class PiRpcSession:
    """One long-lived `pi --mode rpc` subprocess per Guaardvark chat session."""
    def __init__(self, session_id: str, model: str = "", cwd: str = None):
        cmd = ["pi", "--mode", "rpc", "--no-session"]
        if model:
            cmd += ["--model", model]
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=cwd,
        )
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_events, daemon=True)
        self._reader.start()
        self._events: list = []

    def _read_events(self):
        for line in self.proc.stdout:
            line = line.rstrip("\n").rstrip("\r")
            if not line:
                continue
            try:
                self._events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    def send(self, cmd: Dict[str, Any]):
        with self._lock:
            self.proc.stdin.write(json.dumps(cmd) + "\n")
            self.proc.stdin.flush()

    def prompt(self, message: str, streaming_behavior: str = "followUp"):
        self.send({"type": "prompt", "message": message,
                   "streamingBehavior": streaming_behavior})

    def drain_text(self) -> str:
        """Collect text_delta events until agent_settled, return the reply."""
        parts = []
        for ev in self._events:
            if ev.get("type") == "message_update":
                a = ev.get("assistantMessageEvent") or {}
                if a.get("type") == "text_delta":
                    parts.append(a.get("delta", ""))
        return "".join(parts)

    def close(self):
        self.proc.terminate()
```

### Wiring into Guaardvark (provider path)

1. Add to `CLOUD_PROVIDERS` in `llm_provider.py`:
   ```python
   PI = "pi"
   CLOUD_PROVIDERS[PI] = {
       "label": "pi agent (RPC)",
       "key_env": "PI_RPC_ENABLED",
       "available_fn": lambda: True,  # pi is local; gate on binary presence
   }
   ```
2. Implement `chat()` (yield Ollama-shaped `{"message": {"content": tok}, "done": bool}`
   chunks) and `make_llamaindex_llm()` (a `CustomLLM` whose `.chat()` calls the session).
3. Set the active provider to `pi` and the model to a pi model id.

### Streaming event → Socket.IO mapping

| pi RPC event | Guaardvark emit |
|---|---|
| `message_update` / `text_delta` | `chat:token` `{"content": delta}` |
| `message_update` / `thinking_delta` | `chat:thinking` (suppress from visible tokens) |
| `tool_execution_start/update/end` | `chat:tool_call` / `chat:tool_result` |
| `agent_settled` | `chat:complete` |

### Caveats / gotchas

- **Tool-call contract:** pi manages its own tools; Guaardvark's XML/JSON tool-call
  parsing is bypassed. The bridge must surface pi's `toolcall_*` events as Guaardvark
  tool events, not feed them back as text.
- **Context ownership:** pi owns context/compaction. Guaardvark's `_compact_history`,
  memory injection, and tool selection should be **disabled** for this path or they'll
  fight pi's own management.
- **One process per session:** keep a `PiRpcSession` per Guaardvark `session_id`;
  reuse it for follow-ups (`pi-reply` semantics via the same process).
- **Streaming concurrency:** a `prompt` while pi is streaming needs `streamingBehavior`;
  use `steer` for mid-turn redirection, `followUp` to queue.
- **`--no-session`** avoids pi writing session files; use `--session-dir` if you want
  persistence.

### Verification

1. `pi --mode rpc` and send `{"type":"prompt","message":"hi"}` — confirm events stream.
2. In Guaardvark, set provider to `pi`, send a chat message, confirm `chat:token` streams
   and `chat:complete` fires on `agent_settled`.
3. Test a tool-using prompt (e.g. "list files") and confirm `tool_execution_*` events
   surface as Guaardvark tool events.

### Related
- `docs/ARCHITECTURE.md` — AgentBrain tiers, memory, tool registry.
- `CAPABILITIES.md` — feature/model enumeration.
- `backend/services/llm_provider.py`, `mistral_provider.py`, `openai_provider.py` —
  provider dispatch and cloud routing.
- pi RPC docs: `packages/coding-agent/docs/rpc.md` (pi-mono repo).
- `pi-mcp-server` (npm) — lighter alternative: wraps pi as MCP tools; Guaardvark can
  connect as an MCP client (`mcp_connect`/`mcp_execute`) with no code changes.
