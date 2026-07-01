from __future__ import annotations
import ctypes
import json
import logging
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _set_pdeathsig():
    """preexec_fn: ask the kernel to SIGKILL this daemon when its parent (the
    celery worker that spawned it) dies. The trainer daemon is a plain Popen
    child with no lifecycle link to the worker, so a worker restart/crash/recycle
    used to ORPHAN it — and an orphaned daemon keeps ~7GB of SDXL resident,
    OOMing the next training job until someone kills it by hand. PR_SET_PDEATHSIG
    closes that gap: parent gone → daemon dies → VRAM freed automatically.

    Linux-only (prctl); a silent no-op on other platforms. Runs in the forked
    child between fork() and exec(), so it must stay tiny and lock-free — a single
    libc call is safe; touching Python-level locks here is not."""
    if sys.platform != "linux":
        return
    PR_SET_PDEATHSIG = 1
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
    except Exception:
        # Best-effort: if prctl is unavailable the daemon simply behaves as before
        # (the per-job shutdown() still reaps it on normal completion).
        pass

class RealLoraTrainer:
    """Spawns and drives plugins/lora_trainer/scripts/run_trainer.py inside
    venv-torch. Subprocess is lazily started on first use.
    
    Public API matches mock_trainer.train_subject_lora's contract so the
    selector in lora_trainer_tasks._train_impl can swap them."""

    _PLUGIN_ROOT = Path(__file__).resolve().parent
    _RUNNER_SCRIPT = _PLUGIN_ROOT / "scripts" / "run_trainer.py"
    _VENV_PYTHON = _PLUGIN_ROOT / "venv-torch" / "bin" / "python"
    _LOAD_TIMEOUT_S = 900    # SDXL is ~7 GB on first download
    _TRAIN_TIMEOUT_S = 1800  # 30 min cap per subject

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._loaded = False

    @staticmethod
    def _gpu_env() -> dict:
        """Environment for the trainer subprocesses, immune to the backend's own
        CPU-forcing env poison.

        The backend sets ``CUDA_VISIBLE_DEVICES=''`` IN-PROCESS to push RAG /
        embeddings / indexing onto the CPU (see indexing_service.py,
        llama_index_local_config.py, gpu_embedding). A celery worker that has
        touched that code carries the empty value in its os.environ for the rest
        of its life — and a child spawned with the default (inherited) env would
        see ZERO GPUs (torch.cuda.device_count() == 0), which is exactly the
        "CUDA probe failed" that blocked subject 16's amend runs even on a free
        card. We force a real device for our GPU subprocess. An explicit non-empty
        value (a real multi-GPU pin) is respected; only ''/unset is repaired.
        """
        import os
        env = dict(os.environ)
        if not env.get("CUDA_VISIBLE_DEVICES"):  # '' or missing → the poisoned case
            env["CUDA_VISIBLE_DEVICES"] = "0"
        return env

    @classmethod
    def is_available(cls) -> bool:
        """True iff venv-torch/bin/python exists AND it can actually see a CUDA GPU.
        This prevents picking the REAL backend on systems that have the venv
        but no working NVIDIA driver/CUDA runtime visible to torch (common
        cause of the "CUDA not available" failure on capable hardware like
        RTX 4070 Ti SUPER).
        """
        if not cls._VENV_PYTHON.exists():
            return False
        # Probe a few times before giving up. A single reading can be a transient
        # false-negative — right after a host restart the NVIDIA stack is still
        # settling, and a fresh torch import can momentarily race driver init or
        # brief GPU contention while other services spin up. That blip used to
        # block a real training run (subject 16, 2026-06-25, two failed clicks
        # while the probe passed seconds later from a shell). The GPU gate already
        # serializes real contention, so retrying here only filters out the blip;
        # if CUDA is genuinely unavailable, all attempts fail and we still bail.
        attempts = 3
        last = ""
        for i in range(attempts):
            try:
                probe = subprocess.run(
                    [
                        str(cls._VENV_PYTHON),
                        "-c",
                        "import torch, sys; "
                        "ok = torch.cuda.is_available() and torch.cuda.device_count() > 0; "
                        "print('OK' if ok else 'NO'); "
                        "print(torch.cuda.get_device_name(0) if ok else '', file=sys.stderr)",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,  # cold torch+CUDA init after a restart can exceed 8s
                    env=cls._gpu_env(),  # immune to the worker's CUDA_VISIBLE_DEVICES='' poison
                )
                if probe.returncode == 0 and "OK" in probe.stdout:
                    if i:
                        logger.info("real_trainer: CUDA probe OK on attempt %d/%d", i + 1, attempts)
                    return True
                last = f"stdout={probe.stdout.strip()!r} stderr={probe.stderr.strip()!r}"
            except Exception as e:
                last = f"probe crashed: {e}"
            if i < attempts - 1:
                time.sleep(2)  # let the driver/contention settle, then retry
        logger.warning(
            "real_trainer: venv exists but CUDA probe failed after %d attempts: %s",
            attempts, last,
        )
        return False

    def _ensure_proc(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return

        if not self._VENV_PYTHON.exists():
            raise RuntimeError(f"venv-torch not found at {self._VENV_PYTHON}")
        if not self._RUNNER_SCRIPT.exists():
            raise RuntimeError(f"Trainer script missing at {self._RUNNER_SCRIPT}")

        logger.info("Spawning LoRA trainer daemon: %s %s", self._VENV_PYTHON, self._RUNNER_SCRIPT)
        self._proc = subprocess.Popen(
            [str(self._VENV_PYTHON), "-u", str(self._RUNNER_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(self._PLUGIN_ROOT),
            # Force a real GPU device: the worker may carry CUDA_VISIBLE_DEVICES=''
            # from the backend's CPU-forced RAG/embeddings, which the daemon would
            # otherwise inherit and train blind (or fail). See _gpu_env().
            env=self._gpu_env(),
            # Die with the worker so a restart/crash can't orphan a 7GB daemon.
            preexec_fn=_set_pdeathsig,
        )

        def _pump_stderr():
            log_file = self._PLUGIN_ROOT.parent.parent / "logs" / "lora_trainer_daemon.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, "a") as f:
                if self._proc and self._proc.stderr:
                    for line in self._proc.stderr:
                        line = line.rstrip()
                        if line:
                            logger.info("lora_trainer daemon: %s", line)
                            f.write(line + "\n")
                            f.flush()

        threading.Thread(target=_pump_stderr, daemon=True).start()

        pong = self._send({"op": "ping"}, timeout_s=10)
        if not pong.get("ok"):
            self._kill_proc()
            raise RuntimeError(f"LoRA trainer daemon ping failed: {pong}")

    def _send(self, msg: dict, timeout_s: float) -> dict:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise RuntimeError("LoRA trainer daemon not running")

        line = json.dumps(msg) + "\n"
        try:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise RuntimeError(f"LoRA trainer daemon stdin closed: {e}") from e

        result_holder: dict[str, Any] = {}

        def _watchdog() -> None:
            time.sleep(timeout_s)
            if not result_holder:
                logger.error("LoRA trainer daemon timed out after %ss; killing", timeout_s)
                self._kill_proc()

        threading.Thread(target=_watchdog, daemon=True).start()

        response_line = self._proc.stdout.readline()
        result_holder["done"] = True

        if not response_line:
            raise RuntimeError("LoRA trainer daemon closed stdout (likely crashed or timed out)")

        try:
            return json.loads(response_line)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"LoRA trainer daemon returned non-JSON: {response_line!r} ({e})") from e

    def train_subject_lora(
        self,
        *,
        subject_id: int,
        subject_name: str,
        ref_image_paths: list[str],
        output_dir: str,
        trigger_word: str | None = None,
        resolution: int = 768,
        image_prompts: list[str] | None = None,
        rank: int = 16,
        alpha: int = 16,
        learning_rate: float = 1.0e-4,
        steps: int | None = None,
        **_,
    ) -> dict:
        # resolution is the dominant VRAM lever for SDXL LoRA. Default 768 fits a
        # 16 GB card alongside the bf16 + gradient-checkpointing the runner already
        # does; bump to 1024 on a 24 GB+ card for sharper identity. Snap to /64 so
        # the UNet doesn't choke on an odd size.
        resolution = max(512, (int(resolution) // 64) * 64)
        if not ref_image_paths:
            return {"status": "failed", "error": "no reference images provided"}

        # The token the LoRA actually learns. Prefer the explicit rare trigger
        # (e.g. "sage_harlow"); fall back to the display name. Whatever we train
        # on here MUST be what generation prompts with — see ComfyUIImageGenerator.
        token = (trigger_word or "").strip() or subject_name

        with self._lock:
            try:
                self._ensure_proc()
            except Exception as e:
                return {"status": "failed", "error": str(e)}
            
            # Absolute path is mandatory: the daemon subprocess runs with
            # cwd=plugin root, so a relative output_dir (e.g. "data/training/
            # loras") would resolve under plugins/lora_trainer/ and the save
            # would land nowhere / fail. Resolve against the backend cwd here.
            target_dir = Path(output_dir).resolve()
            target_dir.mkdir(parents=True, exist_ok=True)
            safe_name = "".join(c if c.isalnum() else "_" for c in subject_name) or "subject"
            
            # Find next version
            v = 1
            while (target_dir / f"{safe_name}_v{v}.safetensors").exists():
                v += 1
            output_path = target_dir / f"{safe_name}_v{v}.safetensors"

            if not self._loaded:
                load_resp = self._send({"op": "load", "model_id": "stabilityai/stable-diffusion-xl-base-1.0"}, timeout_s=self._LOAD_TIMEOUT_S)
                if not load_resp.get("ok"):
                    return {"status": "failed", "error": load_resp.get("error", "load failed")}
                self._loaded = True

            if steps is None:
                steps = min(1500, max(400, len(ref_image_paths) * 100))
            prompts = list(image_prompts or [])
            if not prompts:
                prompts = [f"a photo of {token}"] * len(ref_image_paths)
            while len(prompts) < len(ref_image_paths):
                prompts.append(prompts[-1] if prompts else f"a photo of {token}")

            train_resp = self._send({
                "op": "train",
                "params": {
                    "subject_id": subject_id,
                    "subject_name": subject_name,
                    "ref_image_paths": ref_image_paths,
                    "output_path": str(output_path),
                    "rank": int(rank),
                    "alpha": int(alpha),
                    "steps": int(steps),
                    "learning_rate": float(learning_rate),
                    "resolution": resolution,
                    "seed": 42,
                    "instance_prompt": f"a photo of {token}",
                    "image_prompts": prompts[: len(ref_image_paths)],
                }
            }, timeout_s=self._TRAIN_TIMEOUT_S)

            if not train_resp.get("ok"):
                return {"status": "failed", "error": train_resp.get("error", "train failed")}

            sidecar = output_path.with_suffix(".json")
            sidecar.write_text(json.dumps({
                "subject_id": subject_id,
                "subject_name": subject_name,
                "trigger_word": token,
                "instance_prompt": f"a photo of {token}",
                "ref_count": len(ref_image_paths),
                "mock": False,
                "steps": steps,
            }))

            return {
                "status": "ok",
                "lora_path": str(output_path),
                "lora_version": v
            }

    def shutdown(self) -> None:
        if self._proc is None:
            return
        try:
            self._send({"op": "shutdown"}, timeout_s=15)
        except Exception as e:
            logger.warning("LoRA trainer daemon graceful shutdown failed (%s); killing", e)
        try:
            self._proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, AttributeError):
            self._kill_proc()
        self._proc = None
        self._loaded = False
        logger.info("LoRA trainer daemon stopped")

    def _kill_proc(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.kill()
            self._proc.wait(timeout=5)
        except Exception:
            pass
        self._proc = None
        self._loaded = False

_TRAINER = RealLoraTrainer()
