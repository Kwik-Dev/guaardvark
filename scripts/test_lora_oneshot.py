#!/usr/bin/env python3
"""
One-shot LoRA training test — the real gate for the film-orchestrator consistency
pipeline (docs/plans/2026-06-02-film-production-orchestrator.md, Layer 2 / P0.5).

WHAT IT PROVES: that an SDXL character LoRA actually trains to completion on a
16 GB card at resolution 768 (the knob we just threaded into real_trainer.py),
and how much VRAM it really peaks at — the one thing that can't be verified
without the GPU.

RUN IT (on the box with the 4070 Ti SUPER):
    # stop Ollama first so the VRAM reading is clean (it can hold ~11 GB):
    #   curl -s http://localhost:11434/api/... or just `./stop.sh` the ollama plugin
    backend/venv/bin/python scripts/test_lora_oneshot.py --refs /path/to/vampire_cowboy_stills

    # bigger card? push resolution up:
    backend/venv/bin/python scripts/test_lora_oneshot.py --refs <dir> --resolution 1024

NOTES:
  - First run downloads SDXL base (~7 GB) — that's a one-time cost, not VRAM.
  - Peak VRAM is sampled from nvidia-smi (whole-GPU), so close other GPU users
    for a clean number. Verdict compares against 16 GB.
  - Training runs in the plugin's venv-torch subprocess; this script just drives it.
"""

import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRAINER_DIR = REPO_ROOT / "plugins" / "lora_trainer"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VRAM_BUDGET_MB = 16376  # the 4070 Ti SUPER's total, from nvidia-smi


def _gpu_used_mb():
    """Current total GPU memory used (MiB), or None if nvidia-smi is unavailable."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True, timeout=5,
        )
        return int(out.strip().splitlines()[0])
    except Exception:
        return None


class _VramSampler(threading.Thread):
    """Polls nvidia-smi in the background and remembers the peak."""
    def __init__(self, interval=2.0):
        super().__init__(daemon=True)
        self.interval = interval
        self.peak = 0
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            used = _gpu_used_mb()
            if used and used > self.peak:
                self.peak = used
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()


def main():
    ap = argparse.ArgumentParser(description="One-shot SDXL character-LoRA training test.")
    ap.add_argument("--refs", required=True, help="Directory of reference images for the character.")
    ap.add_argument("--name", default="vampire_cowboy_test", help="Subject name.")
    ap.add_argument("--trigger", default="vmpcwby", help="Rare trigger token the LoRA learns.")
    ap.add_argument("--resolution", type=int, default=768, help="Training resolution (768 = 16 GB-safe).")
    ap.add_argument("--out", default=str(REPO_ROOT / "data" / "outputs" / "lora_test"),
                    help="Output dir for the .safetensors LoRA.")
    args = ap.parse_args()

    # Import the real trainer from the plugin (not a package — add its dir to path).
    sys.path.insert(0, str(TRAINER_DIR))
    try:
        from real_trainer import RealLoraTrainer  # noqa: E402
    except Exception as e:
        print(f"✗ Could not import RealLoraTrainer: {e}")
        return 2

    if not RealLoraTrainer.is_available():
        print(f"✗ venv-torch not found at {RealLoraTrainer._VENV_PYTHON}")
        print("  The trainer's isolated torch venv isn't set up on this machine.")
        return 2

    refs_dir = Path(args.refs).expanduser().resolve()
    if not refs_dir.is_dir():
        print(f"✗ --refs is not a directory: {refs_dir}")
        return 2
    ref_paths = sorted(str(p) for p in refs_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not ref_paths:
        print(f"✗ No images ({', '.join(sorted(IMAGE_EXTS))}) found in {refs_dir}")
        return 2

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print("─" * 64)
    print(f"  refs        : {refs_dir}  ({len(ref_paths)} images)")
    print(f"  subject     : {args.name}   trigger: {args.trigger}")
    print(f"  resolution  : {args.resolution}  (snapped to /64 inside the trainer)")
    print(f"  output      : {out_dir}")
    baseline = _gpu_used_mb()
    print(f"  GPU in use now: {baseline} MiB  (close Ollama/other GPU users for a clean peak)")
    print("─" * 64)
    print("  Training… first run downloads SDXL base (~7 GB), be patient.\n")

    sampler = _VramSampler()
    sampler.start()
    t0 = time.time()
    trainer = RealLoraTrainer()
    try:
        result = trainer.train_subject_lora(
            subject_id=999,
            subject_name=args.name,
            ref_image_paths=ref_paths,
            output_dir=str(out_dir),
            trigger_word=args.trigger,
            resolution=args.resolution,
        )
    finally:
        sampler.stop()
        try:
            trainer.shutdown()
        except Exception:
            pass
    dur = time.time() - t0

    print("\n" + "─" * 64)
    ok = result.get("status") == "ok"
    print(f"  RESULT      : {'✓ PASS' if ok else '✗ FAIL'}")
    if ok:
        print(f"  lora_path   : {result.get('lora_path')}")
    else:
        print(f"  error       : {result.get('error')}")
    print(f"  duration    : {dur/60:.1f} min")
    if sampler.peak:
        headroom = VRAM_BUDGET_MB - sampler.peak
        fit = "FITS 16 GB" if sampler.peak < VRAM_BUDGET_MB else "OVER 16 GB — drop --resolution"
        print(f"  peak VRAM   : {sampler.peak} MiB  ({headroom:+d} MiB headroom → {fit})")
    print("─" * 64)
    if ok and sampler.peak and sampler.peak >= VRAM_BUDGET_MB:
        print("  ⚠ Trained but peaked at/over budget — retry with --resolution 640 or 512.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
