"""Hardware/use-case-aware Ollama Modelfile generator.

Produces an ``ollama create`` Modelfile for a chosen base model + sampling
profile, adapted to the machine it runs on. Built for the WHOLE Guaardvark
install base, not one box (no GPU / one GPU / many GPUs; coding vs creative
users) — it detects VRAM and sizes the context window accordingly rather than
hard-coding this workstation's 16 GB.

Design principles enforced here:
  * Sampling knobs come from services.sampling_profiles (single source of truth),
    so a baked model matches what the app passes at runtime. See that module's
    docstring for the override gotcha.
  * The base model's chat template, renderer/parser, and stop tokens are
    INHERITED via ``FROM`` — never hand-rolled. Modern bases ship their own turn
    handling (e.g. gemma4 uses ``RENDERER gemma4`` / ``PARSER gemma4`` and has no
    stop tokens); imposing ChatML ``stop "<|im_end|>"`` on them is incorrect.
  * num_ctx is the ONE thing we size per hardware here (the runtime path lets
    ollama_resource_manager size it dynamically; a baked model needs a concrete
    ceiling, so we pick a safe one for the detected tier).

This is the hardware/profile-aware generator. The standalone training script
services/training/scripts/finetune_model.py::create_ollama_modelfile now draws
its sampling knobs from the same source (services.sampling_profiles) instead of
the old hard-coded temp0.4/top_p0.8/top_k30, but stays deliberately simpler — it
runs in an isolated training venv, so it has no hardware sizing and uses a static
SYSTEM. Prefer THIS module for in-app, hardware-adaptive Modelfile creation.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.services import sampling_profiles

logger = logging.getLogger(__name__)

# Per-profile baked SYSTEM prompts. Generic-but-useful; in-app the dynamic
# system message the chat engine sends takes precedence (a system role in the
# messages array overrides the Modelfile SYSTEM), so these mainly benefit direct
# CLI / MCP / `ollama run` callers. None => omit SYSTEM entirely.
_PROFILE_SYSTEM: Dict[str, Optional[str]] = {
    sampling_profiles.PRECISE: (
        "You are a precise, expert software engineer. Provide correct, concise, "
        "modular code and structured output. Prefer exact answers over filler; "
        "when emitting JSON/YAML, emit only valid, parseable output."
    ),
    sampling_profiles.BALANCED: None,
    sampling_profiles.CREATIVE: (
        "You are an imaginative, articulate creative collaborator. Offer vivid, "
        "varied ideas and prose; avoid repetition and canned phrasing."
    ),
    sampling_profiles.RAG: (
        "You answer strictly from the provided context. Synthesize sources into "
        "a direct answer; if the context does not contain the answer, say so "
        "rather than inventing one."
    ),
}


# --- Hardware detection -----------------------------------------------------

def detect_vram_gb() -> List[float]:
    """Return per-GPU VRAM in GB (best effort). Empty list => no NVIDIA GPU.

    Uses nvidia-smi; intentionally dependency-free so it works on a fresh
    install before the Python GPU stack is present.
    """
    smi = shutil.which("nvidia-smi")
    if not smi:
        return []
    try:
        out = subprocess.run(
            [smi, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return []
        gpus: List[float] = []
        for line in out.stdout.strip().splitlines():
            line = line.strip()
            if line:
                gpus.append(round(int(line) / 1024.0, 1))  # MiB -> GiB
        return gpus
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.debug("VRAM detection failed: %s", exc)
        return []


def hardware_tier(vram_gb: Optional[List[float]] = None) -> str:
    """Classify the machine into a coarse tier from its largest single GPU.

    Tiers: 'cpu' (no GPU), 'low' (<6GB), 'mid' (6-11GB), 'high' (12-23GB),
    'ultra' (24GB+). A model loads on one GPU, so the largest single card
    decides the ceiling — not the sum.
    """
    if vram_gb is None:
        vram_gb = detect_vram_gb()
    if not vram_gb:
        return "cpu"
    top = max(vram_gb)
    if top < 6:
        return "low"
    if top < 12:
        return "mid"
    if top < 24:
        return "high"
    return "ultra"


# Baked num_ctx ceiling per (tier, is_rag). Chat profiles don't need huge
# windows; the RAG profile gets a bigger one where VRAM allows. These are safe
# defaults — a model that won't fit simply gets a smaller window, never an OOM.
_NUM_CTX: Dict[str, Tuple[int, int]] = {
    # tier: (chat_ctx, rag_ctx)
    "cpu": (4096, 8192),
    "low": (4096, 8192),
    "mid": (8192, 16384),
    "high": (8192, 32768),
    "ultra": (16384, 65536),
}


def recommend_num_ctx(profile: str, tier: Optional[str] = None) -> int:
    """Pick a baked num_ctx for a profile given the hardware tier."""
    tier = tier or hardware_tier()
    chat_ctx, rag_ctx = _NUM_CTX.get(tier, _NUM_CTX["mid"])
    return rag_ctx if profile == sampling_profiles.RAG else chat_ctx


# --- Modelfile construction -------------------------------------------------

def base_uses_builtin_renderer(base_model: str) -> bool:
    """True if the base declares RENDERER/PARSER (new Ollama format).

    Such models own their turn handling; we must not add manual stop tokens.
    Best effort: on any failure we assume True (the safe choice — inherit, don't
    impose stops).
    """
    try:
        out = subprocess.run(
            ["ollama", "show", "--modelfile", base_model],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0:
            return True
        text = out.stdout
        return ("RENDERER" in text) or ("PARSER" in text)
    except Exception:
        return True


def build_modelfile(
    base_model: str,
    profile: str = sampling_profiles.DEFAULT_PROFILE,
    *,
    system_prompt: Optional[str] = None,
    num_ctx: Optional[int] = None,
    tier: Optional[str] = None,
    extra_params: Optional[Dict[str, Any]] = None,
) -> str:
    """Build a Modelfile string for ``base_model`` tuned to ``profile``.

    The base template / renderer / stops are inherited via ``FROM``; we only add
    sampling PARAMETERs, a num_ctx sized for the hardware, and an optional SYSTEM.
    """
    if not sampling_profiles.has_profile(profile):
        profile = sampling_profiles.DEFAULT_PROFILE
    if num_ctx is None:
        num_ctx = recommend_num_ctx(profile, tier)
    if system_prompt is None:
        system_prompt = _PROFILE_SYSTEM.get(profile)

    lines: List[str] = [
        f"# Guaardvark-generated Modelfile — base={base_model} profile={profile}",
        f"FROM {base_model}",
        "",
        f"PARAMETER num_ctx {int(num_ctx)}",
        sampling_profiles.profile_modelfile_params(profile),
    ]
    for key, value in (extra_params or {}).items():
        lines.append(f"PARAMETER {key} {value}")
    if system_prompt:
        lines += ["", f'SYSTEM """{system_prompt}"""']
    return "\n".join(lines) + "\n"


def create_model(
    base_model: str,
    new_name: str,
    profile: str = sampling_profiles.DEFAULT_PROFILE,
    *,
    system_prompt: Optional[str] = None,
    num_ctx: Optional[int] = None,
    write_to: Optional[str] = None,
) -> Tuple[bool, str]:
    """Generate a Modelfile and run ``ollama create`` (no inference — cheap/safe).

    Returns (ok, combined_output). ``write_to`` optionally persists the Modelfile
    for inspection; otherwise a temp file is used.
    """
    content = build_modelfile(
        base_model, profile, system_prompt=system_prompt, num_ctx=num_ctx
    )

    if write_to:
        path = Path(write_to)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        mf_path = str(path)
        cleanup = False
    else:
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".Modelfile", delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.close()
        mf_path = tmp.name
        cleanup = True

    try:
        proc = subprocess.run(
            ["ollama", "create", new_name, "-f", mf_path],
            capture_output=True, text=True, timeout=300,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        ok = proc.returncode == 0
        if not ok:
            logger.warning("ollama create %s failed: %s", new_name, output.strip())
        return ok, output.strip()
    finally:
        if cleanup:
            try:
                Path(mf_path).unlink()
            except OSError:
                pass
