"""Audio Foundry weights: what is on this machine, and the install path.

Generation must never fetch from Hugging Face. This module is the catalog the
Audio Studio modal reads, plus the explicit snapshot_download Install flow.
Flask owns both so listing and download work when the sidecar is stopped.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DOWNLOAD_STALL_SECONDS = 180
_HF_XET_ENV = "HF_HUB_DISABLE_XET"

# MiniMax Music 3 lives in the video registry (ComfyUI/models). One Install
# click here forwards to that downloader so Audio Studio is self-contained.
MINIMAX_MUSIC3_ID = "minimax-music3-int8"

# Sizes are the sum of the files Install fetches, from the Hugging Face file
# listings read 2026-09-15. ``allow_patterns`` / ``ignore_patterns`` go to
# snapshot_download: the Chatterbox repo is 13.9 GB of language variants of
# which the loader reads five files (3.3 GB); Stable Audio Open ships the same
# weights three times (15.7 GB, 5.3 GB without the root .ckpt/.safetensors).
AUDIO_FOUNDRY_MODELS: List[Dict[str, Any]] = [
    {
        "id": "chatterbox",
        "name": "Chatterbox TTS",
        "description": "Expressive voiceover and reference-clip cloning. 3.3 GB.",
        "group": "voice",
        "hf_repo": "ResembleAI/chatterbox",
        "probe_file": "t3_cfg.safetensors",
        "allow_patterns": [
            "ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors",
            "tokenizer.json", "conds.pt",
        ],
        "size_gb": 3.3,
        "gated": False,
    },
    {
        "id": "kokoro",
        "name": "Kokoro-82M",
        "description": "Fast built-in voices (fallback when Chatterbox is off). 360 MB.",
        "group": "voice",
        "hf_repo": "hexgrad/Kokoro-82M",
        "probe_file": "config.json",
        "size_gb": 0.36,
        "gated": False,
    },
    {
        "id": "ace-step",
        "name": "ACE-Step v1 3.5B",
        "description": "Full songs with vocals from a style prompt and lyrics. 8.3 GB.",
        "group": "music",
        "hf_repo": "ACE-Step/ACE-Step-v1-3.5B",
        "probe_file": "ace_step_transformer/config.json",
        "size_gb": 8.3,
        "gated": False,
    },
    {
        "id": "stable-audio-open",
        "name": "Stable Audio Open 1.0",
        "description": "Sound effects, ambience and music beds up to 47 s. 5.3 GB. Gated on Hugging Face.",
        "group": "fx",
        "hf_repo": "stabilityai/stable-audio-open-1.0",
        "probe_file": "model_index.json",
        "ignore_patterns": ["model.ckpt", "model.safetensors", "vae_model.ckpt", "*.png"],
        "size_gb": 5.3,
        "gated": True,
        "terms_url": "https://huggingface.co/stabilityai/stable-audio-open-1.0",
    },
]

_download_lock = threading.Lock()
_download_epoch = 0
_download_state: Dict[str, Any] = {
    "is_downloading": False,
    "current_id": None,
    "progress": 0,
    "status": "idle",
    "speed_mbps": 0,
    "downloaded_gb": 0,
    "total_gb": 0,
    "error": None,
    "delegated": None,
}


def reset_download_state() -> None:
    """Test helper — clear the in-process download lock."""
    global _download_epoch
    with _download_lock:
        _download_epoch = 0
        _download_state.update({
            "is_downloading": False,
            "current_id": None,
            "progress": 0,
            "status": "idle",
            "speed_mbps": 0,
            "downloaded_gb": 0,
            "total_gb": 0,
            "error": None,
            "delegated": None,
            "updated_at": 0,
            "epoch": 0,
        })


def get_entry(model_id: str) -> Optional[Dict[str, Any]]:
    for row in AUDIO_FOUNDRY_MODELS:
        if row["id"] == model_id:
            return row
    if model_id == MINIMAX_MUSIC3_ID:
        return {"id": MINIMAX_MUSIC3_ID, "delegate": "video"}
    return None


def is_hub_cached(repo_id: str, probe_file: str) -> bool:
    from backend.services.local_weights import is_cached
    return is_cached(repo_id, probe_file)


def hf_token_present() -> bool:
    from backend.services.user_model_families import hf_token_present as _present
    return _present()


def plugin_snapshot() -> Dict[str, Any]:
    """audio_foundry enable/run state. Never starts the plugin."""
    try:
        from backend.plugins.plugin_base import PluginStatus
        from backend.plugins.plugin_manager import get_plugin_manager

        pm = get_plugin_manager()
        status = pm.get_status("audio_foundry")
        value = status.value if hasattr(status, "value") else str(status)
        running = status == PluginStatus.RUNNING
        enabled = bool(pm.is_effectively_enabled("audio_foundry"))
        return {"running": running, "enabled": enabled, "status": value}
    except Exception as e:
        logger.debug("audio_foundry plugin snapshot failed: %s", e)
        return {"running": False, "enabled": False, "status": "unknown"}


def _minimax_row() -> Dict[str, Any]:
    from backend.api.batch_video_generation_api import (
        _check_model_downloaded,
        _missing_check_files,
        _resolve_download_plan,
    )
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY

    info = VIDEO_MODEL_REGISTRY[MINIMAX_MUSIC3_ID]
    plan = _resolve_download_plan(MINIMAX_MUSIC3_ID)
    ready = all(_check_model_downloaded(eid) for eid in plan)
    size = round(sum(VIDEO_MODEL_REGISTRY[eid]["size_gb"] for eid in plan), 2)
    missing = _missing_check_files(MINIMAX_MUSIC3_ID) if not ready else []
    return {
        "id": MINIMAX_MUSIC3_ID,
        "name": info["name"],
        "description": (
            f"{info['description']} Installs into ComfyUI — same files as Manage Video Models."
        ),
        "group": "music",
        "hf_repo": info["hf_repo"],
        "probe_file": None,
        "size_gb": size,
        "gated": False,
        "terms_url": None,
        "installed": ready,
        "delegate": "video",
        "missing_files": missing,
    }


def _hub_row(entry: Dict[str, Any]) -> Dict[str, Any]:
    installed = is_hub_cached(entry["hf_repo"], entry["probe_file"])
    return {
        "id": entry["id"],
        "name": entry["name"],
        "description": entry["description"],
        "group": entry["group"],
        "hf_repo": entry["hf_repo"],
        "probe_file": entry["probe_file"],
        "size_gb": entry["size_gb"],
        "gated": bool(entry.get("gated")),
        "terms_url": entry.get("terms_url"),
        "installed": installed,
        "delegate": None,
        "missing_files": [] if installed else [entry["probe_file"]],
    }


def list_models() -> Dict[str, Any]:
    models = [_hub_row(e) for e in AUDIO_FOUNDRY_MODELS]
    try:
        models.append(_minimax_row())
    except Exception as e:
        logger.warning("MiniMax Music 3 row failed: %s", e)
    return {"plugin": plugin_snapshot(), "models": models}


def download_status() -> Dict[str, Any]:
    with _download_lock:
        st = dict(_download_state)
    if st.get("delegated") == "video":
        return _video_status_as_audio(st)
    return {"success": True, **{k: v for k, v in st.items() if k != "delegated"}}


def _video_status_as_audio(fallback: Dict[str, Any]) -> Dict[str, Any]:
    from backend.api import batch_video_generation_api as video_api

    with video_api._video_model_download_lock:
        vs = dict(video_api._video_model_download_status)
    return {
        "success": True,
        "is_downloading": bool(vs.get("is_downloading")),
        "current_id": vs.get("current_model") or fallback.get("current_id"),
        "progress": vs.get("progress") or 0,
        "status": vs.get("status") or "idle",
        "speed_mbps": vs.get("speed_mbps") or 0,
        "downloaded_gb": vs.get("downloaded_gb") or 0,
        "total_gb": vs.get("total_gb") or fallback.get("total_gb") or 0,
        "error": vs.get("error"),
    }


def _hf_repo_cache_dir(repo_id: str) -> Path:
    from backend.services.local_weights import hf_repo_cache_dir
    return hf_repo_cache_dir(repo_id)


def _dir_bytes(d: Path) -> int:
    total = 0
    if not d.exists():
        return 0
    # snapshots/<rev>/ holds symlinks into blobs/; count each blob once.
    for f in d.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def start_download(model_id: str) -> tuple:
    """Start an Install. Returns (payload_dict, http_status)."""
    global _download_epoch

    entry = get_entry(model_id)
    if not entry:
        return {"success": False, "error": f"unknown model id: {model_id}"}, 400

    with _download_lock:
        st = _download_state
        if st.get("is_downloading"):
            return {
                "success": False,
                "error": f"already downloading {st.get('current_id')}",
            }, 409

    if entry.get("delegate") == "video" or model_id == MINIMAX_MUSIC3_ID:
        return _start_minimax_download()

    hub_entry = next(e for e in AUDIO_FOUNDRY_MODELS if e["id"] == model_id)
    if hub_entry.get("gated") and not hf_token_present():
        terms = hub_entry.get("terms_url") or f"https://huggingface.co/{hub_entry['hf_repo']}"
        return {
            "success": False,
            "error": (
                f"{hub_entry['name']} is gated on Hugging Face. "
                f"1) Accept terms at {terms}. "
                "2) Set HF_TOKEN in .env and restart the backend."
            ),
        }, 400

    if is_hub_cached(hub_entry["hf_repo"], hub_entry["probe_file"]):
        return {
            "success": True,
            "already_installed": True,
            "id": model_id,
        }, 200

    with _download_lock:
        _download_epoch += 1
        epoch = _download_epoch
        _download_state.update({
            "is_downloading": True,
            "current_id": model_id,
            "progress": 0,
            "status": "starting",
            "speed_mbps": 0,
            "downloaded_gb": 0,
            "total_gb": float(hub_entry["size_gb"]),
            "error": None,
            "delegated": None,
            "updated_at": time.time(),
            "epoch": epoch,
        })

    threading.Thread(
        target=_run_snapshot_download,
        args=(hub_entry, epoch),
        daemon=True,
        name=f"audio-foundry-dl-{model_id}",
    ).start()
    return {"success": True, "status": "started", "id": model_id}, 200


def _start_minimax_download() -> tuple:
    from backend.api.batch_video_generation_api import start_video_model_download

    resp, status = start_video_model_download(MINIMAX_MUSIC3_ID)
    body = resp.get_json() if hasattr(resp, "get_json") else {}
    if status == 409:
        err = (body.get("error") or {}).get("message") or body.get("error") or "already downloading"
        return {"success": False, "error": err}, 409
    if status != 200 or not body.get("success"):
        err = (body.get("error") or {}).get("message") or body.get("error") or "download failed"
        return {"success": False, "error": err}, status or 500

    data = body.get("data") or {}
    message = (body.get("message") or data.get("message") or "").lower()
    if data.get("already_installed") or "already installed" in message:
        return {"success": True, "already_installed": True, "id": MINIMAX_MUSIC3_ID}, 200

    with _download_lock:
        _download_state.update({
            "is_downloading": True,
            "current_id": MINIMAX_MUSIC3_ID,
            "progress": 0,
            "status": "starting",
            "speed_mbps": 0,
            "downloaded_gb": 0,
            "total_gb": float((_minimax_row() or {}).get("size_gb") or 11.09),
            "error": None,
            "delegated": "video",
            "updated_at": time.time(),
        })
    return {"success": True, "status": "started", "id": MINIMAX_MUSIC3_ID}, 200


def _run_snapshot_download(entry: Dict[str, Any], epoch: int) -> None:
    os.environ.setdefault(_HF_XET_ENV, "1")
    repo_id = entry["hf_repo"]
    dest = _hf_repo_cache_dir(repo_id)
    dest.mkdir(parents=True, exist_ok=True)
    baseline = _dir_bytes(dest)
    total_bytes = int(float(entry["size_gb"]) * 1024**3) or 1
    started = time.time()
    stalled = threading.Event()
    last_repo = repo_id

    def _is_current() -> bool:
        return _download_state.get("epoch") == epoch

    def _update(**kw) -> bool:
        with _download_lock:
            if not _is_current():
                return False
            _download_state.update(kw)
            _download_state["updated_at"] = time.time()
            return True

    stop_monitor = threading.Event()

    def _monitor() -> None:
        last_bytes = -1
        last_change = time.time()
        while not stop_monitor.is_set():
            try:
                downloaded = max(0, _dir_bytes(dest) - baseline)
                now = time.time()
                if downloaded != last_bytes:
                    last_bytes = downloaded
                    last_change = now
                elif (now - last_change) > _DOWNLOAD_STALL_SECONDS:
                    stalled.set()
                    # is_downloading stays True until the worker exits (finally
                    # below), so a retry cannot start a second snapshot_download
                    # into the same cache directory.
                    _update(
                        status="failed",
                        progress=0,
                        error=(
                            f"Download stalled — no progress for {_DOWNLOAD_STALL_SECONDS}s. "
                            "Check your network and click Install to retry."
                        ),
                    )
                    stop_monitor.set()
                    break
                elapsed = max(now - started, 0.1)
                speed = (downloaded / (1024 * 1024)) / elapsed
                pct = min(int((downloaded / max(total_bytes, 1)) * 100), 99)
                _update(
                    status="downloading",
                    progress=pct,
                    speed_mbps=round(speed, 1),
                    downloaded_gb=round(downloaded / 1024**3, 2),
                )
            except Exception:
                pass
            stop_monitor.wait(1.0)

    monitor = threading.Thread(target=_monitor, daemon=True)
    monitor.start()
    try:
        from huggingface_hub import snapshot_download

        _update(status="downloading")
        snapshot_download(
            repo_id=repo_id,
            allow_patterns=entry.get("allow_patterns"),
            ignore_patterns=entry.get("ignore_patterns"),
        )
        if not is_hub_cached(repo_id, entry["probe_file"]):
            raise RuntimeError(
                f"Download of {entry['id']} finished but {entry['probe_file']} is still missing"
            )
        if stalled.is_set():
            return
        _update(
            progress=100,
            downloaded_gb=float(entry["size_gb"]),
            status="completed",
            is_downloading=False,
        )
        logger.info("Audio Foundry model downloaded: %s", entry["id"])
    except Exception as e:
        logger.error("Audio Foundry download failed: %s", e, exc_info=True)
        if not stalled.is_set():
            from backend.services.video_model_registry import classify_hf_download_error
            _update(
                status="failed",
                error=classify_hf_download_error(e, repo_id=last_repo),
                progress=0,
                is_downloading=False,
            )
    finally:
        stop_monitor.set()
        monitor.join(timeout=2)
        with _download_lock:
            if _download_state.get("epoch") == epoch and _download_state.get("is_downloading"):
                _download_state["is_downloading"] = False
                _download_state["updated_at"] = time.time()
