# scripts/platform/ — detect-then-route launcher pattern (PROPOSAL)

**Status: proposal / not wired.** These files exist for review. Nothing sources them yet,
so the current `start.sh` boot is **unchanged** (zero risk to the working Linux/CUDA path).
Created 2026-06-19 as the agreed design for cross-platform install (Pi aarch64 / macOS arm64 /
Windows-WSL), per `docs/local-workspace-only/CROSS_PLATFORM_SOLUTIONS_2026-06-19.md`.

## Why this shape (vs 3 separate start.sh files)
~90% of `start.sh` is platform-agnostic (venv, pip, migrations, Flask/Celery/frontend, health,
agent display). Only ~10% is platform-specific. Three forked `start.sh` files would duplicate the
90% → they drift → drift is what caused the bugs we just fixed (inverted Python gate, KJNodes/core
version mismatch). So: **one thin orchestrator + pluggable platform backends sharing one interface.**
This just extends the pattern `scripts/install_pytorch.sh` already uses (arch-branching helper).

## The pieces
| File | Role |
|------|------|
| `detect.sh` | `detect_platform()` → sets `GUAARDVARK_OS/_ARCH/_ACCEL/_IS_WSL` + picks the backend. Pure detection. |
| `linux.sh` | Linux backend (x86_64 + Pi-arm64 + WSL). apt + systemctl. |
| `macos.sh` | macOS backend (Apple Silicon primary, Intel untested). Homebrew + brew services; no systemd/sudoers/nvidia. |
| `hardware_policy.platform_profile()` | the auto-detected "machine config" — ONE brain shared by bash + Python (see below). |

Pi = `linux.sh` with `ARCH=arm64`. WSL = `linux.sh` with `IS_WSL=1`. Only two backend files.

## The interface (every backend implements these identically)
```
platform_install_system_deps    # postgres/redis/ffmpeg/node/build-tools (apt | brew)
# Note: full Video Editor + music video also needs `melt` (MLT) + Shotcut. See plugins/video_editor/README.md "Linux & macOS Setup".
platform_ensure_python           # guarantee Python 3.12; sets PYTHON_CMD (apt | deadsnakes | uv/pyenv | brew)
platform_gpu_setup               # nvidia tuning on Linux+CUDA; no-op on mac/cpu
platform_service_start <svc>     # systemctl | brew services | (WSL/no-systemd fallback)
```
torch is NOT in the interface — `scripts/install_pytorch.sh` already branches Mac/ROCm/CUDA/CPU.

## Proposed `start.sh` wiring (NOT applied — review first)
Near the top, after `SCRIPT_DIR` is set and the vader_* helpers are defined:
```bash
source "$SCRIPT_DIR/scripts/platform/detect.sh"
detect_platform
vader_info "Platform: $GUAARDVARK_OS/$GUAARDVARK_ARCH accel=$GUAARDVARK_ACCEL wsl=$GUAARDVARK_IS_WSL"
source "$GUAARDVARK_PLATFORM_BACKEND"
```
Then replace the scattered apt/systemctl/python-gate inline blocks with calls to
`platform_install_system_deps` / `platform_ensure_python` / `platform_gpu_setup` /
`platform_service_start`. **Incremental rollout:** wire `macos.sh` first (all-new code, guarded by
`[ "$GUAARDVARK_OS" = macos ]`), leave the existing Linux inline path untouched; extract Linux into
`linux.sh` later as an optional symmetry pass. Run `./start.sh --test` after each step.

## Proposed addition to `backend/services/hardware_policy.py` (the shared config brain — NOT applied)
`hardware_policy.py` is already the stdlib-only SSOT (emits `torch_channel`, `ollama_env`,
`model_tier`, writes `~/.guaardvark/hardware.json`). Add a `platform_profile()` so the **bash
launcher and the Python runtime read ONE detected profile** and never disagree about the host:
```python
def platform_profile() -> dict:
    """One detected platform profile shared by start.sh (bash) and the runtime (Python).
    Stdlib-only; JSON-emittable. Auto-detected, NOT a hand-written per-machine file
    (offline-first 'works on any install' charter; .env can override individual knobs)."""
    import platform as _p
    osname = {"Linux": "linux", "Darwin": "macos"}.get(_p.system(), "unknown")
    mach = _p.machine().lower()
    arch = "x86_64" if mach in ("x86_64", "amd64") else ("arm64" if mach in ("aarch64", "arm64") else mach)
    gpu = detect_gpu()  # existing helper
    accel = ("cuda" if gpu.get("vendor") == "nvidia"
             else "rocm" if gpu.get("vendor") == "amd"
             else "mps" if (osname == "macos" and arch == "arm64")
             else "cpu")
    return {
        "os": osname, "arch": arch, "accel": accel,
        "service_mgr": "launchd" if osname == "macos" else "systemd",
        "python_target": "3.12",
        "model_tier": model_tier(...),            # existing
        "media_models": media_models(gpu, arch),  # generative-media-engineer proposal (image/video routing)
    }
# add a CLI arm: `python -m backend.services.hardware_policy platform_profile` → prints JSON,
# which start.sh can read to cross-check its own coarse detect.
```

## Verify (per target, after wiring)
`./start.sh --test` on: NVIDIA x86 (regression guard — must stay green), Pi5/Debian13 (3.12 via uv),
macOS arm64 (3.12 via brew, MPS torch). WSL = the Linux path + `IS_WSL` service fallback.
