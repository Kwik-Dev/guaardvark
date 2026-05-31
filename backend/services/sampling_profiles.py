"""Single source of truth for LLM sampling profiles.

Read by, as of 2026-05-31:
  * utils/llm_service.py — default Ollama construction (get_default_llm,
    get_llm_for_startup, and the saved-model loader) via _default_chat_sampling.
  * services/unified_chat_engine.py — runtime streaming-chat ``options``.
  * services/modelfile_generator.py — baked ``PARAMETER`` lines for the
    hardware-aware ``ollama create`` path.
  * services/training/scripts/finetune_model.py — baked params for fine-tuned
    GGUF imports (with a literal fallback for the standalone training venv).
A profile defined once therefore behaves identically whether the model is
invoked with runtime ``options`` or baked into an ``ollama create`` Modelfile.
(agent_brain does not read profiles directly — it runs whatever llm instance it
is handed, which is built here.)

WHY THIS MODULE EXISTS — the gotcha that makes tuning real instead of cosmetic:
Ollama runtime ``options`` OVERRIDE Modelfile ``PARAMETER`` directives. The app
historically hard-coded ``temperature 0.4 / top_p 0.8 / top_k 30`` scattered
across three modules, so any value baked into a Modelfile was silently ignored
in-app. Centralizing the definition here keeps the baked model and the runtime
call in lockstep — change a number once and every caller (chat, CLI, MCP,
agents) plus the generated Modelfile move together.

SCOPE — profiles are PURE SAMPLING knobs only:
  * temperature, top_p, top_k, min_p, repeat_penalty, presence/frequency penalty
They deliberately do NOT carry:
  * num_ctx        -> owned by utils/ollama_resource_manager.compute_optimal_num_ctx
                      (hardware-adaptive; a static Modelfile value would fight it).
  * stop / template -> owned by the base model's renderer/template, inherited via
                      ``FROM``. Modern bases ship their own turn handling: e.g.
                      gemma4:e4b uses ``RENDERER gemma4`` / ``PARSER gemma4`` and
                      has NO stop tokens. Hand-rolling ChatML ``stop "<|im_end|>"``
                      onto such a model is incorrect. Trust the base format.

Values derived from KDnuggets "Tweaking Local LLM Settings with Ollama"
(docs/local-workspace-only/), adapted for Guaardvark's use-case axes. The min_p
caveat is respected throughout: when min_p is set, top_p is kept high (>=0.95)
so it does not interfere with min_p's dynamic scaling.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

# Canonical profile names. Map to the three use-case axes from the design note:
# precise (coding/structured), balanced (default chat), creative, rag (long-ctx).
PRECISE = "precise"
BALANCED = "balanced"
CREATIVE = "creative"
RAG = "rag"

DEFAULT_PROFILE = BALANCED

# Each profile is the full set of sampling options passed to Ollama. Only keys
# present here are sent; absent keys fall back to the model's own defaults.
_PROFILES: Dict[str, Dict[str, Any]] = {
    # Deterministic, structured output: code generation, JSON/YAML extraction,
    # tool-call arguments, factual summarization. Low temp + tight pruning.
    PRECISE: {
        "temperature": 0.1,
        "min_p": 0.05,
        "top_p": 0.95,
        "top_k": 20,
        "repeat_penalty": 1.1,
    },
    # Everyday chat default. Replaces the old scattered temp0.4/top_p0.8/top_k30.
    # top_p raised to 0.95 (was 0.8) so it no longer fights min_p; min_p +
    # repeat_penalty added for tail-token safety the old config lacked.
    BALANCED: {
        "temperature": 0.5,
        "min_p": 0.05,
        "top_p": 0.95,
        "top_k": 40,
        "repeat_penalty": 1.1,
    },
    # Brainstorming, story generation, expressive agent dialogue. High temp with
    # stronger penalties to keep diversity from collapsing into loops.
    CREATIVE: {
        "temperature": 0.9,
        "min_p": 0.08,
        "top_p": 0.98,
        "top_k": 60,
        "repeat_penalty": 1.2,
        "presence_penalty": 0.15,
        "frequency_penalty": 0.1,
    },
    # Long-context retrieval / multi-file reading. Low-ish temp for faithfulness
    # to sources, slightly higher repeat_penalty to avoid looping on long prompts.
    # num_ctx is intentionally NOT here — the resource manager sizes it.
    RAG: {
        "temperature": 0.3,
        "min_p": 0.05,
        "top_p": 0.95,
        "top_k": 40,
        "repeat_penalty": 1.15,
    },
}

# Short human-readable descriptions, surfaced to a future profile-picker UI / CLI.
PROFILE_DESCRIPTIONS: Dict[str, str] = {
    PRECISE: "Deterministic — coding, JSON/structured extraction, tool calls.",
    BALANCED: "Default — balanced everyday chat.",
    CREATIVE: "Expressive — brainstorming, story generation, lively agents.",
    RAG: "Faithful — long-context retrieval and multi-file reading.",
}


def list_profiles() -> List[str]:
    """Return the canonical profile names."""
    return list(_PROFILES.keys())


def has_profile(name: Optional[str]) -> bool:
    return bool(name) and name in _PROFILES


def get_profile(name: Optional[str] = None, *, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a fresh copy of a profile's sampling options.

    Unknown / falsy ``name`` falls back to DEFAULT_PROFILE. ``overrides`` are
    shallow-merged on top (a caller wanting one knob nudged without forking the
    profile).
    """
    key = name if has_profile(name) else DEFAULT_PROFILE
    opts = copy.deepcopy(_PROFILES[key])
    if overrides:
        opts.update(overrides)
    return opts


def profile_options(
    name: Optional[str] = None,
    *,
    num_ctx: Optional[int] = None,
    num_predict: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a runtime Ollama ``options`` dict for a profile.

    This is what the chat call sites should pass as ``options=``. ``num_ctx``
    (from compute_optimal_num_ctx) and ``num_predict`` (max tokens) are runtime
    concerns layered on top of the pure sampling profile. ``extra`` allows
    per-call additions (e.g. ``num_keep``) without mutating the profile.
    """
    opts = get_profile(name)
    if num_ctx is not None:
        opts["num_ctx"] = int(num_ctx)
    if num_predict is not None:
        opts["num_predict"] = int(num_predict)
    if extra:
        opts.update(extra)
    return opts


def profile_modelfile_params(name: Optional[str] = None) -> str:
    """Render a profile's sampling knobs as Ollama Modelfile ``PARAMETER`` lines.

    Used by the Modelfile generator so a baked model carries the SAME numbers a
    runtime call would. num_ctx and stop are NOT emitted here (see module docs).
    """
    opts = get_profile(name)
    lines = [f"PARAMETER {key} {value}" for key, value in opts.items()]
    return "\n".join(lines)
