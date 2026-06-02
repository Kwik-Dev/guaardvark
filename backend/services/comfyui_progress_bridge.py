# backend/services/comfyui_progress_bridge.py
#
# Layer 1 of the film-orchestrator plan (docs/plans/2026-06-02-film-production-orchestrator.md).
#
# THE PROBLEM: comfyui_video_generator._wait_for_completion() polls /history/{id},
# which stays silent until the whole job is DONE. Meanwhile ComfyUI is shouting
# per-step progress into its /ws websocket that nobody was listening to. So the UI
# showed a spinner and a prayer for 20 minutes.
#
# THE FIX (additive, flag-gated): connect to that websocket with the same client_id
# we send on the /prompt POST, translate ComfyUI's "progress"/"executing" messages
# into the EXISTING unified progress rail (emit_progress_event -> Redis
# guaardvark:progress -> app.py relay -> 'job_progress' socket event), and let the
# frontend's UnifiedProgressContext (already listening) render it.
#
# This NEVER replaces the /history poll — that stays the source of truth for
# completion + results. The bridge is progress-only and self-terminating: if the
# socket dies, or ComfyUI says "executing: null" (idle/done), or it outlives its
# max lifetime, the thread ends on its own. Worst case it adds nothing; it can't
# wedge a generation.
#
# Protocol verified against the bundled ComfyUI source (2026-06-02):
#   server.py:249   -> ws connects at /ws?clientId=<id>
#   server.py:883   -> /prompt accepts "client_id" in the JSON body
#   server.py:1143  -> envelope is {"type": event, "data": data}
#   main.py:296,299 -> progress: {"value": k, "max": N, "prompt_id": ..., "node": <id>}
#   server.py:266   -> executing: {"node": <id or None>}

import json
import logging
import os
import threading
import time
from typing import Dict, Optional

import websocket  # websocket-client 1.8.0 — already in backend venv

from backend.utils.progress_emitter import emit_progress_event

logger = logging.getLogger(__name__)


def ws_progress_enabled() -> bool:
    """Master switch. ON by default; set GUAARDVARK_COMFYUI_WS_PROGRESS=0 to fall
    back to poll-only (the pre-bridge behavior). This is the ROLLBACK lever."""
    return os.environ.get("GUAARDVARK_COMFYUI_WS_PROGRESS", "1") not in ("0", "false", "False", "")


# class_type substring -> human stage label. ComfyUI only tells us the node *id*
# over the wire, so we resolve id -> class_type via the workflow, then class_type
# -> label here. First match wins, so order matters (specific before generic).
_STAGE_LABELS = [
    ("VHS", "encoding video"),
    ("VideoCombine", "encoding video"),
    ("RIFE", "interpolating"),
    ("FILM", "interpolating"),
    ("Interpolat", "interpolating"),
    ("Upscale", "upscaling"),
    ("VAEDecode", "decoding"),
    ("VAEEncode", "encoding latents"),
    ("KSampler", "denoising"),
    ("Sampler", "denoising"),
    ("TextEncode", "encoding prompt"),
    ("LoraLoader", "loading LoRA"),
    ("CheckpointLoader", "loading model"),
    ("UNETLoader", "loading model"),
    ("CLIPLoader", "loading model"),
    ("Loader", "loading model"),
]


def _label_for(class_type: str) -> str:
    for needle, label in _STAGE_LABELS:
        if needle.lower() in class_type.lower():
            return label
    return class_type or "working"


class ComfyUIProgressBridge:
    """One bridge per generation. start() spins a daemon ws-listener thread;
    stop() asks it to quit. The thread also self-terminates, so a missed stop()
    (e.g. an early return in the caller) never leaks."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._ws: Optional[websocket.WebSocket] = None

    def start(
        self,
        client_id: str,
        process_id: str,
        comfy_url: str,
        workflow: Dict,
        *,
        max_seconds: int = 7200,
        extra: Optional[Dict] = None,
    ) -> None:
        if not ws_progress_enabled():
            return
        # Build node_id -> friendly label map from the workflow we're about to queue.
        node_labels: Dict[str, str] = {}
        try:
            for nid, node in (workflow or {}).items():
                node_labels[str(nid)] = _label_for(node.get("class_type", ""))
        except Exception:
            pass  # a weird workflow just means generic labels; not worth failing for

        ws_url = comfy_url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
        ws_url = f"{ws_url}/ws?clientId={client_id}"

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(ws_url, process_id, node_labels, max_seconds, extra or {}),
            name=f"comfy-ws-{process_id}",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"ComfyUI ws progress bridge started for process {process_id}")

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    # ── the listener thread ──────────────────────────────────────────────────
    def _run(self, ws_url: str, process_id: str, node_labels: Dict[str, str],
             max_seconds: int, extra: Dict) -> None:
        deadline = time.time() + max_seconds
        try:
            # connect timeout small; recv timeout lets us check _stop / deadline.
            self._ws = websocket.create_connection(ws_url, timeout=10)
            self._ws.settimeout(5)
        except Exception as e:
            logger.warning(f"ws progress bridge could not connect ({e}); falling back to poll-only")
            return

        last_pct = -1
        try:
            while not self._stop.is_set() and time.time() < deadline:
                try:
                    raw = self._ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue  # no message this tick — loop to re-check stop/deadline
                except Exception as e:
                    logger.debug(f"ws progress bridge recv ended: {e}")
                    break
                if not raw or not isinstance(raw, str):
                    continue  # binary frames are preview images — ignore
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                mtype = msg.get("type")
                data = msg.get("data", {}) or {}

                if mtype == "progress":
                    value = data.get("value", 0)
                    total = data.get("max", 0) or 0
                    node = str(data.get("node", ""))
                    stage = node_labels.get(node, "working")
                    # Clamp to 1..99 — completion is owned by the /history poll,
                    # never by the bridge (avoids a premature "100%" race).
                    pct = int(value / total * 100) if total else 0
                    pct = max(1, min(99, pct))
                    if pct != last_pct:
                        last_pct = pct
                        emit_progress_event(
                            process_id=process_id,
                            progress=pct,
                            message=f"{stage} {value}/{total}",
                            status="processing",
                            process_type="video_render",
                            additional_data={"stage": stage, "node": node, **extra},
                        )

                elif mtype == "executing":
                    # node == None means the prompt finished / queue went idle.
                    if data.get("node") is None:
                        logger.debug(f"ws progress bridge: ComfyUI idle for {process_id}, stopping")
                        break
        finally:
            try:
                if self._ws is not None:
                    self._ws.close()
            except Exception:
                pass
            self._ws = None
