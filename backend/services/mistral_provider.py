"""
Mistral cloud LLM provider.

Guaardvark is Ollama-first, but this module lets the user route chat generation
to Mistral's hosted API instead (selectable at runtime via the ``llm_provider``
setting — see :mod:`backend.services.llm_provider`).

Two surfaces are exposed so both call sites in the codebase work unchanged:

1. :func:`chat` — mimics ``ollama.chat``'s streaming interface, yielding chunks
   shaped like ``{"message": {"content": "..."}}`` with a final ``done=True``
   chunk carrying token counts. ``unified_chat_engine._call_llm_streaming``
   dispatches to this, so its token-emit / XML-tool-call / token-count loop runs
   untouched.
2. :class:`MistralLLM` — a LlamaIndex ``CustomLLM`` exposing ``.chat()`` /
   ``.complete()``, so the ``llm_service`` helpers (and anything holding the
   active ``self.llm``) route through too.

Tool calling in this codebase is XML-in-the-prompt (see
``backend.utils.agent_output_parser.parse_tool_calls_xml``), not native
function-calling, so the provider only has to stream text — no tool-schema
translation is needed.

Only the standard library + ``requests`` are used (already a dependency); no
Mistral SDK is pulled in.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterator, List, Optional

import requests

from backend import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Availability + config helpers
# ---------------------------------------------------------------------------
def available() -> bool:
    """True when a Mistral API key is configured."""
    return bool(config.MISTRAL_API_KEY)


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {config.MISTRAL_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _map_options(options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Translate Ollama-style sampling options to Mistral chat params.

    Ollama-only knobs (num_ctx, num_keep, top_k, repeat_penalty, …) have no
    Mistral equivalent and are dropped.
    """
    out: Dict[str, Any] = {}
    if not options:
        return out
    if options.get("temperature") is not None:
        out["temperature"] = options["temperature"]
    if options.get("top_p") is not None:
        out["top_p"] = options["top_p"]
    # Ollama caps generation with num_predict; Mistral uses max_tokens. A negative
    # num_predict means "unbounded" in Ollama — omit max_tokens in that case.
    np = options.get("num_predict")
    if isinstance(np, int) and np > 0:
        out["max_tokens"] = np
    return out


def _normalize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Coerce incoming messages to Mistral's ``[{role, content}]`` shape.

    Mistral accepts roles system/user/assistant/tool. Anything else (or an
    Ollama 'thinking' payload) collapses to a plain content string.
    """
    norm: List[Dict[str, str]] = []
    for m in messages:
        role = (m.get("role") or "user").strip()
        if role not in ("system", "user", "assistant", "tool"):
            role = "user"
        content = m.get("content", "")
        if content is None:
            content = ""
        norm.append({"role": role, "content": str(content)})
    return norm


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------
def list_models() -> List[Dict[str, Any]]:
    """Return chat-capable Mistral models as ``[{"name", "id"}]``.

    Mirrors the shape ``/api/model/list`` returns for Ollama so the frontend can
    render them the same way. Falls back to a small static list if the API call
    fails (e.g. offline), so the picker is never empty when a key is set.
    """
    fallback = [
        {"name": "mistral-large-latest", "id": "mistral-large-latest"},
        {"name": "mistral-small-latest", "id": "mistral-small-latest"},
        {"name": "codestral-latest", "id": "codestral-latest"},
        {"name": "open-mistral-nemo", "id": "open-mistral-nemo"},
    ]
    if not available():
        return []
    try:
        resp = requests.get(
            f"{config.MISTRAL_BASE_URL}/models",
            headers=_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        models = []
        for entry in data:
            mid = entry.get("id")
            if not mid:
                continue
            caps = entry.get("capabilities", {}) or {}
            # Skip embedding/moderation-only models; keep anything that can complete chat.
            if caps and caps.get("completion_chat") is False:
                continue
            if "embed" in mid.lower():
                continue
            models.append({"name": mid, "id": mid})
        models.sort(key=lambda m: m["name"])
        return models or fallback
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not list Mistral models, using fallback list: %s", e)
        return fallback


# ---------------------------------------------------------------------------
# Streaming chat — Ollama-shaped, drop-in for ollama.chat()
# ---------------------------------------------------------------------------
def chat(
    model: str,
    messages: List[Dict[str, Any]],
    stream: bool = True,
    options: Optional[Dict[str, Any]] = None,
    **_kwargs: Any,
):
    """Call Mistral's chat completions endpoint.

    Returns chunks shaped exactly like ``ollama.chat``:
      - streaming: a generator of ``{"message": {"content": tok}, "done": bool}``
        with a final ``done=True`` chunk carrying ``prompt_eval_count`` /
        ``eval_count``.
      - non-streaming: a single dict in the same shape with ``done=True``.
    """
    if not available():
        raise RuntimeError("Mistral provider selected but MISTRAL_API_KEY is not set.")

    model = model or config.MISTRAL_DEFAULT_MODEL
    payload: Dict[str, Any] = {
        "model": model,
        "messages": _normalize_messages(messages),
        "stream": bool(stream),
        **_map_options(options),
    }

    if not stream:
        resp = requests.post(
            f"{config.MISTRAL_BASE_URL}/chat/completions",
            headers=_headers(),
            json=payload,
            timeout=config.MISTRAL_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        content = ""
        try:
            content = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            content = ""
        usage = body.get("usage", {}) or {}
        return {
            "message": {"content": content},
            "done": True,
            "prompt_eval_count": usage.get("prompt_tokens", 0) or 0,
            "eval_count": usage.get("completion_tokens", 0) or 0,
        }

    return _stream_chat(payload)


def _stream_chat(payload: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """Yield Ollama-shaped chunks from Mistral's SSE stream."""
    prompt_tokens = 0
    completion_tokens = 0
    with requests.post(
        f"{config.MISTRAL_BASE_URL}/chat/completions",
        headers=_headers(),
        json=payload,
        stream=True,
        timeout=config.MISTRAL_REQUEST_TIMEOUT,
    ) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            if not raw.startswith("data:"):
                continue
            data = raw[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            # Usage is reported on the final chunk(s).
            usage = obj.get("usage")
            if usage:
                prompt_tokens = usage.get("prompt_tokens", prompt_tokens) or prompt_tokens
                completion_tokens = usage.get("completion_tokens", completion_tokens) or completion_tokens
            try:
                delta = obj["choices"][0].get("delta", {}) or {}
            except (KeyError, IndexError, TypeError):
                delta = {}
            token = delta.get("content") or ""
            if token:
                yield {"message": {"content": token}, "done": False}
    # Final Ollama-style done chunk with token counts.
    yield {
        "message": {"content": ""},
        "done": True,
        "prompt_eval_count": prompt_tokens,
        "eval_count": completion_tokens,
    }


def complete(prompt: str, model: Optional[str] = None, options: Optional[Dict[str, Any]] = None) -> str:
    """Non-streaming single-prompt convenience wrapper returning plain text."""
    result = chat(
        model=model or config.MISTRAL_DEFAULT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        stream=False,
        options=options,
    )
    return (result.get("message", {}) or {}).get("content", "") or ""


# ---------------------------------------------------------------------------
# LlamaIndex-compatible wrapper (for llm_service / self.llm callers)
# ---------------------------------------------------------------------------
def make_llamaindex_llm(model: Optional[str] = None):
    """Build a LlamaIndex ``CustomLLM`` backed by Mistral, or None if unavailable.

    Imported lazily so the module has no hard LlamaIndex dependency at import time.
    """
    if not available():
        return None
    try:
        from llama_index.core.llms import (
            CustomLLM,
            CompletionResponse,
            CompletionResponseGen,
            LLMMetadata,
        )
        from llama_index.core.llms.callbacks import llm_completion_callback
        from llama_index.core.base.llms.types import ChatMessage, ChatResponse, MessageRole
    except Exception as e:  # noqa: BLE001
        logger.error("LlamaIndex not available for MistralLLM wrapper: %s", e)
        return None

    resolved_model = model or config.MISTRAL_DEFAULT_MODEL

    class MistralLLM(CustomLLM):
        model: str = resolved_model
        context_window: int = 32000
        num_output: int = 4096

        @property
        def metadata(self) -> "LLMMetadata":
            return LLMMetadata(
                context_window=self.context_window,
                num_output=self.num_output,
                model_name=self.model,
                is_chat_model=True,
            )

        @llm_completion_callback()
        def complete(self, prompt: str, **kwargs: Any) -> "CompletionResponse":
            text = complete(prompt, model=self.model)
            return CompletionResponse(text=text)

        @llm_completion_callback()
        def stream_complete(self, prompt: str, **kwargs: Any) -> "CompletionResponseGen":
            acc = ""
            for chunk in chat(self.model, [{"role": "user", "content": prompt}], stream=True):
                tok = (chunk.get("message", {}) or {}).get("content", "")
                if tok:
                    acc += tok
                    yield CompletionResponse(text=acc, delta=tok)

        def chat(self, messages, **kwargs: Any) -> "ChatResponse":
            dict_messages = [
                {"role": getattr(m.role, "value", str(m.role)), "content": m.content or ""}
                for m in messages
            ]
            result = globals()["chat"](self.model, dict_messages, stream=False)
            content = (result.get("message", {}) or {}).get("content", "") or ""
            return ChatResponse(
                message=ChatMessage(role=MessageRole.ASSISTANT, content=content)
            )

    return MistralLLM()
