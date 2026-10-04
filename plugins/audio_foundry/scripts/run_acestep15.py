"""ACE-Step 1.5 subprocess daemon.

Runs INSIDE plugins/audio_foundry/venv-music15/, which scripts/setup_music15.sh builds
from the pinned ACE-Step 1.5 release (torch 2.10, transformers 4.57: neither the main
plugin venv nor venv-music can hold them). backends/music_gen_acestep15.py drives it
over the same JSON-line protocol as run_acestep.py.

Commands:
    {"op": "ping"}
        -> {"ok": true, "ready": <bool>}

    {"op": "load"}
        -> {"ok": true} on success
        -> {"ok": false, "error": "..."} on failure

    {"op": "generate",
     "params": {
        "style_prompt": "...",
        "lyrics": "...",
        "instrumental_only": false,
        "duration_s": 60.0,
        "steps": 8,
        "seed": null,                # null: a fresh one; the seed used is reported back
        "thinking": true,            # the 1.7B planner writes metadata and audio codes first
        "out_path": "/abs/path/to/output.wav"
     }}
        -> {"ok": true, "samples": <int>, "channels": <int>, "duration_s": <float>,
            "sample_rate": <int, as written: 48000>, "seed": <int, the seed used>}
        -> {"ok": false, "error": "..."}

    {"op": "unload"}
        -> {"ok": true}

    {"op": "shutdown"}
        -> {"ok": true}, then process exits

Weights are read from <repo>/<local_dir>/checkpoints (backends/acestep15_files.json),
where Install puts them. A missing file is refused here: ACE-Step's own loader answers
one by downloading, from Hugging Face or, after probing www.google.com, from ModelScope.
stdout carries only protocol lines; stderr is the human log the parent forwards.
"""
from __future__ import annotations

import gc
import json
import os
import random
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _PLUGIN_ROOT.parents[1]
_CATALOG = _PLUGIN_ROOT / "backends" / "acestep15_files.json"

_INSTALL_HINT = (
    "Open Audio Studio → Manage models and Install ACE-Step 1.5. "
    "Generation never downloads on its own."
)

# The planner runs on plain PyTorch. ACE-Step's "vllm" backend reserves about 82% of
# VRAM up front, and a 240 s render then fails its own VRAM preflight (needs 1.7 GB,
# 1.5 GB free; measured 2026-10-03 on a 16 GB card). "pt" rendered 240 s in 24 s.
_PLANNER_BACKEND = "pt"

_dit: Any = None
_lm: Any = None
_protocol: Any = None


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _claim_stdout() -> Any:
    """A private copy of stdout for protocol lines, with fd 1 then pointed at stderr.

    ACE-Step prints progress to stdout ("Using precomputed LM hints"), which the
    parent would read as a broken response line.
    """
    sys.stdout.flush()
    fd = os.dup(1)
    os.dup2(2, 1)
    return os.fdopen(fd, "w", encoding="utf-8", buffering=1)


def _respond(payload: dict[str, Any]) -> None:
    out = _protocol or sys.stdout
    out.write(json.dumps(payload) + "\n")
    out.flush()


def catalog() -> dict[str, Any]:
    return json.loads(_CATALOG.read_text(encoding="utf-8"))


def model_root(cat: dict[str, Any]) -> Path:
    """ACE-Step's project root: it reads <root>/checkpoints and keeps a small cache in <root>/.cache."""
    return _REPO_ROOT / cat["local_dir"]


def missing_files(checkpoints: Path, files: list[str]) -> list[str]:
    """Required checkpoint files that are not on disk."""
    return [f for f in files if not (checkpoints / f).is_file()]


def _offline_env(root: Path) -> None:
    """Point ACE-Step at the installed weights and keep every library off the network."""
    os.environ["ACESTEP_PROJECT_ROOT"] = str(root)
    os.environ["ACESTEP_CHECKPOINTS_DIR"] = str(root / "checkpoints")
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"):
        os.environ[name] = "1"
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"


def _block_downloads() -> None:
    """Make ACE-Step's download fallback fail instead of fetching."""
    from acestep import model_downloader

    def _refuse(repo_id, *args, **kwargs):
        return False, f"{repo_id} is not installed here. {_INSTALL_HINT}"

    model_downloader._smart_download = _refuse


def _do_load() -> dict[str, Any]:
    global _dit, _lm
    if _dit is not None:
        return {"ok": True}

    cat = catalog()
    root = model_root(cat)
    missing = missing_files(root / "checkpoints", cat["files"])
    if missing:
        return {
            "ok": False,
            "error": f"ACE-Step 1.5 weights are not on this machine (missing {missing[0]}). {_INSTALL_HINT}",
        }

    _offline_env(root)
    import torch
    if not torch.cuda.is_available():
        return {"ok": False, "error": "No CUDA GPU available; ACE-Step 1.5 here needs an NVIDIA GPU"}

    _block_downloads()
    from acestep.handler import AceStepHandler
    from acestep.llm_inference import LLMHandler

    _eprint(f"[run_acestep15] loading {cat['dit']} + {cat['planner']} from {root / 'checkpoints'}")
    dit = AceStepHandler()
    msg, ok = dit.initialize_service(
        project_root=str(root), config_path=cat["dit"], device="cuda", offload_to_cpu=False,
    )
    if not ok:
        return {"ok": False, "error": f"ACE-Step 1.5 could not load {cat['dit']}: {msg}"}

    lm = LLMHandler()
    msg, ok = lm.initialize(
        checkpoint_dir=str(root / "checkpoints"), lm_model_path=cat["planner"],
        backend=_PLANNER_BACKEND, device="cuda", offload_to_cpu=False, dtype=None,
    )
    if not ok:
        del dit
        _free_cuda()
        return {"ok": False, "error": f"ACE-Step 1.5 could not load its planner {cat['planner']}: {msg}"}

    _dit, _lm = dit, lm
    _eprint(f"[run_acestep15] loaded (dtype={getattr(dit, 'dtype', '?')}, planner={_PLANNER_BACKEND})")
    return {"ok": True}


def _do_generate(params: dict[str, Any]) -> dict[str, Any]:
    if _dit is None:
        return {"ok": False, "error": "model not loaded — send 'load' first"}

    from acestep.inference import GenerationConfig, GenerationParams, generate_music
    import soundfile as sf

    style_prompt = params["style_prompt"]
    instrumental_only = bool(params.get("instrumental_only", False))
    # "[Instrumental]" plus the instrumental flag is how ACE-Step 1.5 documents a track
    # without vocals.
    lyrics = "[Instrumental]" if instrumental_only else (params.get("lyrics") or "")
    duration_s = float(params["duration_s"])
    seed = params.get("seed")
    seed = int(seed) if seed is not None else random.randint(0, 2**32 - 1)
    out_path = Path(params["out_path"])

    gen_params = GenerationParams(
        task_type="text2music",
        caption=style_prompt,
        lyrics=lyrics,
        instrumental=instrumental_only,
        duration=duration_s,
        inference_steps=int(params.get("steps", 8)),
        thinking=bool(params.get("thinking", True)),
    )
    # generate_music takes the DiT's seed from the config, never from
    # GenerationParams.seed. The planner seeds itself only for batches, so with one
    # item it samples from torch's global generator: seeded here, or the same seed
    # plans a different bpm and key and the take cannot be repeated.
    gen_config = GenerationConfig(batch_size=1, audio_format="wav", use_random_seed=False, seeds=[seed])
    import torch
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)

    _eprint(
        f"[run_acestep15] generate style={style_prompt[:60]!r} duration={duration_s:.1f}s "
        f"lyrics={len(lyrics)}-chars instrumental={instrumental_only} seed={seed}"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Same filesystem as out_path, so the move is a rename; anything else ACE-Step
    # writes next to the audio goes with the folder.
    with tempfile.TemporaryDirectory(dir=out_path.parent, prefix=".acestep15-") as tmp:
        result = generate_music(_dit, _lm, params=gen_params, config=gen_config, save_dir=tmp)
        audio = result.audios[0] if result.success and result.audios else {}
        if not audio.get("path"):
            reason = result.error or result.status_message or "no audio came back"
            return {"ok": False, "error": f"ACE-Step 1.5 generation failed: {reason}"}
        shutil.move(audio["path"], out_path)

    info = sf.info(str(out_path))
    rate = int(info.samplerate)
    return {
        "ok": True,
        "samples": int(info.frames),
        "channels": int(info.channels),
        "duration_s": info.frames / rate if rate else 0.0,
        "sample_rate": rate,
        "seed": seed,
    }


def _free_cuda() -> None:
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _do_unload() -> dict[str, Any]:
    global _dit, _lm
    if _dit is None and _lm is None:
        return {"ok": True}
    _dit = None
    _lm = None
    _free_cuda()
    _eprint("[run_acestep15] unloaded")
    return {"ok": True}


def _handle(cmd: dict[str, Any]) -> dict[str, Any]:
    op = cmd.get("op")
    if op == "ping":
        return {"ok": True, "ready": _dit is not None}
    if op == "load":
        return _do_load()
    if op == "generate":
        return _do_generate(cmd["params"])
    if op == "unload":
        return _do_unload()
    if op == "shutdown":
        return {"ok": True}
    return {"ok": False, "error": f"unknown op: {op!r}"}


def main() -> int:
    global _protocol
    _protocol = _claim_stdout()
    _eprint("[run_acestep15] daemon ready, waiting on stdin...")
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError as e:
            _respond({"ok": False, "error": f"bad json: {e}"})
            continue

        try:
            response = _handle(cmd)
        except Exception as e:
            tb = traceback.format_exc()
            _eprint(f"[run_acestep15] handler exception:\n{tb}")
            response = {"ok": False, "error": f"{type(e).__name__}: {e}", "traceback": tb}

        _respond(response)

        if cmd.get("op") == "shutdown":
            _eprint("[run_acestep15] shutdown requested, exiting")
            return 0

    _eprint("[run_acestep15] stdin closed, exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
