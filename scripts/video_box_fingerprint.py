#!/usr/bin/env python3
"""Fingerprint what shapes a Wan 2.2 5B render on this machine, so two machines
can be compared field by field.

The Interconnector syncs Guaardvark's code, but not ComfyUI, its custom nodes,
the model files, .env, the PyTorch build or the browser's settings. When one
machine renders a model cleanly and another renders it garbled, the difference
is in one of those. This prints one JSON document covering them:

  code          sha256 of the files that build the graph, per-file hashes of
                backend/services and plugins/comfyui, and the frontend bundle
                (the git commit only means something on a machine updated by git)
  env           an allowlist of non-secret settings, as .env, this shell and the
                running backend, Celery and ComfyUI processes see them
  runtime       GPU, driver, PyTorch build and the packages ComfyUI imports
  comfyui       version, local edits, command line, /system_stats, the node
                classes the graph uses, and the last start-up and Wan render
                lines of logs/comfyui.log
  custom_nodes  installed revisions against plugins/comfyui/custom_nodes.manifest
  graph         the Wan 5B graph this checkout builds for a fixed request, made by
                the real builder and never submitted
  models        which Wan 5B, VAE, text encoder and RIFE files ComfyUI loads,
                with size, sha256 and the safetensors header
  mp4           the graph VideoHelperSuite embedded in the newest wan22_5b_*.mp4
                (its comment tag), or in each --mp4 FILE
  checks        plain-language notes on anything that looks off

Read-only: it reads files and /proc, runs git, nvidia-smi and ffprobe, and makes
GET requests to a ComfyUI on localhost. It writes nothing except --out.

Examples:
  backend/venv/bin/python scripts/video_box_fingerprint.py --out fingerprint-a.json
  backend/venv/bin/python scripts/video_box_fingerprint.py --no-hash --hash-models wan22-vae
  backend/venv/bin/python scripts/video_box_fingerprint.py --only mp4 --mp4 path/to/clip.mp4
  backend/venv/bin/python scripts/video_box_fingerprint.py --diff fingerprint-a.json fingerprint-b.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

FORMAT = 1
SCRIPT_ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "wan22-5b"
SECTIONS = ("code", "env", "runtime", "comfyui", "custom_nodes", "graph", "models", "mp4")

# Non-secret settings that change how a video is built, launched or decoded.
# Nothing outside this tuple is ever read out of .env or a process environment.
ENV_ALLOWLIST = (
    # ComfyUI launch: plugins/comfyui/scripts/start.sh, comfyui_launch_flags.py
    "GUAARDVARK_COMFYUI_ATTENTION", "GUAARDVARK_COMFYUI_RESERVE_VRAM", "GUAARDVARK_COMFYUI_PINNED_MEMORY",
    "GUAARDVARK_COMFYUI_PREVIEW_METHOD", "GUAARDVARK_COMFYUI_PREVIEW_SIZE", "GUAARDVARK_COMFYUI_LISTEN",
    "GUAARDVARK_COMFYUI_URL", "GUAARDVARK_COMFYUI_DIR", "GUAARDVARK_COMFYUI_VENV",
    "GUAARDVARK_COMFYUI_AUTONODES", "GUAARDVARK_COMFYUI_LORAS_DIR", "GUAARDVARK_COMFYUI_NETWORK_MODE",
    "GUAARDVARK_OOM_SCORE_ADJ",
    # Graph building: comfyui_video_generator.py, video_render_limits.py, profiles
    "GUAARDVARK_WAN5B_SAMPLER", "GUAARDVARK_WAN_CLIP_DEVICE", "WAN_CLIP_DEVICE",
    "GUAARDVARK_VIDEO_REFERENCE_DEFAULTS", "VIDEO_REFERENCE_DEFAULTS", "GUAARDVARK_VIDEO_STRICT_LIMITS",
    "GUAARDVARK_VIDEO_VRAM_WAIT_S", "GUAARDVARK_VIDEO_AUTO_RETRY", "GUAARDVARK_VIDEO_BACKEND",
    "GUAARDVARK_PROFILE", "GUAARDVARK_GPU_QUALITY_TIER", "GUAARDVARK_GPU_HARD_FIT",
    "GUAARDVARK_GPU_POWER_LIMIT",
    # CUDA and PyTorch runtime switches
    "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "CUDA_MODULE_LOADING", "PYTORCH_CUDA_ALLOC_CONF",
    "PYTORCH_ALLOC_CONF", "NVIDIA_TF32_OVERRIDE", "TORCH_ALLOW_TF32_CUBLAS_OVERRIDE",
    "CUBLAS_WORKSPACE_CONFIG",
    # Where files are read and written
    "GUAARDVARK_ROOT", "GUAARDVARK_STORAGE_DIR", "GUAARDVARK_OUTPUT_DIR", "GUAARDVARK_UPLOAD_DIR",
    "COMFYUI_OUTPUT_DIR", "GUAARDVARK_TRAINING_DIR",
    "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
)

# Files whose bytes decide the graph and the ComfyUI launch; hashed in full.
KEY_FILES = (
    "backend/services/comfyui_video_workflows.py",
    "backend/services/comfyui_video_generator.py",
    "backend/services/video_model_registry.py",
    "backend/services/video_render_limits.py",
    "backend/services/comfyui_launch_flags.py",
    "backend/services/user_video_models.py",
    "backend/config.py",
    "plugins/comfyui/scripts/start.sh",
    "plugins/comfyui/scripts/install_deps.sh",
    "plugins/comfyui/custom_nodes.manifest",
    "plugins/comfyui/guaardvark_model_paths.yaml",
    "frontend/src/pages/VideoGeneratorPage.jsx",
    "VERSION",
)
PER_FILE_TREES = ("backend/services", "plugins/comfyui")
DIGEST_TREES = ("backend/api", "backend/tasks", "backend/utils", "backend/profiles", "frontend/src")
SKIP_DIRS = {"__pycache__", "node_modules", ".git", "ComfyUI"}

# ComfyUI sources on the Wan 5B path: loading, sampling, attention, the VAE.
COMFY_SOURCES = (
    "nodes.py", "comfy/cli_args.py", "comfy/model_patcher.py", "comfy/model_management.py",
    "comfy/model_base.py", "comfy/model_detection.py", "comfy/model_sampling.py", "comfy/samplers.py",
    "comfy/sample.py", "comfy/sd.py", "comfy/ops.py", "comfy/quant_ops.py",
    "comfy/ldm/modules/attention.py", "comfy/ldm/wan/model.py", "comfy/ldm/wan/vae2_2.py",
    "comfy/float.py", "comfy/text_encoders/wan.py", "comfy/k_diffusion/sampling.py", "comfy_extras/nodes_wan.py",
    "comfy_extras/nodes_model_advanced.py",
)
# Node classes asked of /object_info; the graph's own classes are added at run time.
COMFY_NODES = ("ModelAttentionBackend", "Wan22ImageToVideoLatent", "VAEDecodeTiled", "VAEDecode",
               "KSampler", "ModelSamplingSD3", "UNETLoader", "CLIPLoader", "VAELoader",
               "VHS_VideoCombine", "RIFE VFI")
# ComfyUI's search order per loader folder (folder_paths.py): the first match wins.
LOADER_FOLDERS = {
    "diffusion_models": ("unet", "diffusion_models"),
    "unet": ("unet", "diffusion_models"),
    "text_encoders": ("text_encoders", "clip"),
    "clip": ("text_encoders", "clip"),
    "vae": ("vae",),
}
PACKAGES = (
    "torch", "torchvision", "torchaudio", "triton", "xformers", "sageattention", "comfy-kitchen",
    "comfy-aimdo", "comfyui-frontend-package", "comfyui-workflow-templates", "numpy", "safetensors",
    "av", "transformers", "tokenizers", "sentencepiece", "spandrel", "einops", "gguf", "kornia",
    "cupy", "cupy-cuda12x", "cupy-cuda11x", "taichi", "opencv-python", "opencv-python-headless",
)

# A fixed request, so two machines' graphs differ only where the machines do.
# Native canvas and length, RIFE doubling as the Studio's default clip uses.
FIXED_REQUEST = {
    "prompt": ("A red fox trots through fresh snow past pine trees in soft morning light, "
               "the camera tracking alongside at shoulder height."),
    "width": 1280, "height": 704, "frames": 121, "steps": 20, "seed": 20260925, "fps": 24,
    "interpolation_multiplier": 2,
}


# ── Small helpers ────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _run(cmd: list, timeout: float = 30, cwd=None, env=None) -> Optional[str]:
    """stdout of a command, or None when it is missing, fails or times out."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _git(repo: Path, *args: str) -> Optional[str]:
    # --no-optional-locks keeps status/describe from rewriting the index.
    return _run(["git", "--no-optional-locks", "-C", str(repo), *args], timeout=30)


def _git_info(repo: Path, limit: int = 40) -> dict:
    if not (repo / ".git").exists():
        return {"git": False}
    status = _git(repo, "status", "--porcelain", "--untracked-files=no") or ""
    lines = [ln for ln in status.splitlines() if ln.strip()]
    return {
        "head": _git(repo, "rev-parse", "HEAD"),
        "describe": _git(repo, "describe", "--tags", "--always", "--dirty"),
        "branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        "modified": lines[:limit] + ([f"... {len(lines) - limit} more"] if len(lines) > limit else []),
    }


def _sha256(path: Path, chunk: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _head_tail_sha256(path: Path, span: int = 4 << 20) -> str:
    """sha256 of the size, first and last 4 MB: cheap, catches truncation and most swaps."""
    size = path.stat().st_size
    h = hashlib.sha256(struct.pack(">Q", size))
    with open(path, "rb") as f:
        h.update(f.read(span))
        if size > span:
            f.seek(max(span, size - span))
            h.update(f.read(span))
    return h.hexdigest()


def _is_under(path, parent) -> bool:
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_URL_USERINFO = re.compile(r"(\w+://)[^/@\s]+@")


class Scrubber:
    """Rewrites this checkout's root and the home directory to placeholders, so
    two machines' paths compare equal, and drops credentials from URLs."""

    def __init__(self, root: Path):
        self.pairs = [(str(root.resolve()), "<root>")]
        home = os.path.expanduser("~")
        if home and home != "/":
            self.pairs.append((home, "~"))

    def text(self, value: str) -> str:
        for old, new in self.pairs:
            value = value.replace(old, new)
        return _URL_USERINFO.sub(r"\1", value)

    def __call__(self, obj):
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return {self.text(str(k)): self(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self(v) for v in obj]
        return obj


# ── Environment (allowlist only) ─────────────────────────────────────────────

def parse_dotenv(path: Path, keys: Iterable[str] = ENV_ALLOWLIST) -> dict:
    """Allowlisted ``KEY=VALUE`` pairs from a .env file; every other line is skipped unread."""
    wanted = set(keys)
    found: dict = {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return found
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in wanted:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        found[key] = value
    return found


def filter_environ(raw: bytes, keys: Iterable[str] = ENV_ALLOWLIST) -> dict:
    """Allowlisted entries of a /proc/<pid>/environ blob."""
    wanted = set(keys)
    out = {}
    for item in raw.split(b"\0"):
        key, sep, value = item.partition(b"=")
        name = key.decode("utf-8", "replace")
        if sep and name in wanted:
            out[name] = value.decode("utf-8", "replace")
    return dict(sorted(out.items()))


def allowlisted(env: Mapping[str, str], keys: Iterable[str] = ENV_ALLOWLIST) -> dict:
    return {k: env[k] for k in keys if k in env}


def _boot_time(proc: Path) -> Optional[float]:
    try:
        for line in (proc / "stat").read_text().splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except OSError:
        pass
    return None


def _process_start(proc: Path, pid: str, btime: Optional[float]) -> Optional[float]:
    if btime is None:
        return None
    try:
        stat = (proc / pid / "stat").read_text()
        fields = stat[stat.rindex(")") + 2:].split()
        return btime + int(fields[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None


def _process_role(args: list, cwd: str, root: Path, comfy_dir: Path) -> Optional[str]:
    if any(Path(a).name == "main.py" for a in args) and _is_under(cwd, comfy_dir):
        return "comfyui"
    if not _is_under(cwd, root) or _is_under(cwd, comfy_dir):
        return None
    if "backend.app" in args or any(a.replace("\\", "/").endswith("backend/app.py") for a in args):
        return "backend"
    if any(Path(a).name == "celery" for a in args):
        if "worker" in args:
            host = next((a.split("=", 1)[1] for a in args if a.startswith("--hostname=")), "")
            return f"celery worker {host.split('@')[0]}".strip()
        if "beat" in args:
            return "celery beat"
    return None


def find_processes(root: Path, comfy_dir: Path, proc: Path = Path("/proc")) -> dict:
    """Backend, Celery and ComfyUI processes of this checkout (by working directory),
    with the allowlisted part of the environment each was started with."""
    found: dict = {}
    btime = _boot_time(proc)
    try:
        pids = sorted((p.name for p in proc.iterdir() if p.name.isdigit()), key=int)
    except OSError:
        return found
    for pid in pids:
        try:
            args = [a.decode("utf-8", "replace") for a in (proc / pid / "cmdline").read_bytes().split(b"\0") if a]
            cwd = os.readlink(proc / pid / "cwd")
        except OSError:
            continue
        role = _process_role(args, cwd, root, comfy_dir)
        if not role:
            continue
        try:
            env = filter_environ((proc / pid / "environ").read_bytes())
        except OSError:
            env = None
        started = _process_start(proc, pid, btime)
        entry = {"pid": int(pid), "started": _iso(started) if started else None,
                 "_started_ts": started, "env": env if env is not None else "not readable"}
        if role == "comfyui":
            entry["argv"] = args
        label, n = role, 2
        while label in found:
            label, n = f"{role} #{n}", n + 1
        found[label] = entry
    return found


def collect_env(ctx: "Context") -> dict:
    dotenv_path = ctx.root / ".env"
    out = {
        "note": ("Only allowlisted keys are read. A process's values are the environment it was "
                 "started with; the backend also loads .env at import without overriding them."),
        "allowlist": list(ENV_ALLOWLIST),
        "dotenv": parse_dotenv(dotenv_path) if dotenv_path.exists() else "no .env",
        "this_shell": allowlisted(ctx.shell_env),
        "processes": {},
    }
    env_mtime = dotenv_path.stat().st_mtime if dotenv_path.exists() else None
    for label, entry in ctx.processes.items():
        shown = {k: v for k, v in entry.items() if not k.startswith("_") and k != "argv"}
        started = entry.get("_started_ts")
        if env_mtime and started:
            shown["dotenv_changed_after_start"] = env_mtime > started
        out["processes"][label] = shown
    return out


# ── Code ─────────────────────────────────────────────────────────────────────

def _walk_files(base: Path):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith((".pyc", ".pyo")):
                continue
            path = Path(dirpath) / name
            if path.is_file():
                yield path


def collect_code(ctx: "Context") -> dict:
    root = ctx.root
    key_files = {}
    for rel in KEY_FILES:
        p = root / rel
        key_files[rel] = _sha256(p) if p.is_file() else None
    per_file, trees = {}, {}
    for rel in PER_FILE_TREES + DIGEST_TREES:
        base = root / rel
        if not base.is_dir():
            trees[rel] = None
            continue
        digest, count = hashlib.sha256(), 0
        for path in _walk_files(base):
            h = _sha256(path)
            name = path.relative_to(root).as_posix()
            digest.update(f"{name}\0{h}\n".encode())
            count += 1
            if rel in PER_FILE_TREES:
                per_file[name] = h[:16]
        trees[rel] = {"files": count, "sha256": digest.hexdigest()}
    dist = root / "frontend" / "dist" / "index.html"
    frontend = {"dist_index_sha256": None, "dist_assets": []}
    if dist.is_file():
        html = dist.read_text(encoding="utf-8", errors="replace")
        frontend = {"dist_index_sha256": _sha256(dist), "dist_built": _iso(dist.stat().st_mtime),
                    "dist_assets": sorted(set(re.findall(r'(?:src|href)="/?(assets/[^"]+)"', html)))}
    version = root / "VERSION"
    return {
        "note": "The Interconnector syncs files, not commits: compare key_files and trees.",
        "version": version.read_text().strip() if version.is_file() else None,
        "git": _git_info(root),
        "key_files": key_files,
        "trees": trees,
        "per_file": per_file,
        "frontend": frontend,
    }


# ── Runtime: GPU, PyTorch, packages ──────────────────────────────────────────

_RUNTIME_PROBE = r"""
import json, sys
from importlib import metadata
wanted = set(json.loads(sys.argv[1]))
out = {"python": sys.version.split()[0], "executable": sys.executable}
pk = {}
for d in metadata.distributions():
    name = (d.metadata.get("Name") or "").lower().replace("_", "-")
    if name in wanted or name.startswith("nvidia-"):
        pk[name] = d.version
out["packages"] = dict(sorted(pk.items()))
try:
    import torch
    t = {"version": torch.__version__, "cuda": torch.version.cuda,
         "git": getattr(torch.version, "git_version", None), "hip": getattr(torch.version, "hip", None)}
    try:
        t["cudnn"] = torch.backends.cudnn.version()
    except Exception as e:
        t["cudnn"] = "error: %s" % str(e)[:120]
    try:
        t["cuda_available"] = torch.cuda.is_available()
        t["arch_list"] = torch.cuda.get_arch_list() if t["cuda_available"] else []
    except Exception as e:
        t["cuda_error"] = str(e)[:200]
    out["torch"] = t
except Exception as e:
    out["torch"] = {"error": str(e)[:200]}
print(json.dumps(out))
"""

_NVIDIA_FIELDS = ("index", "name", "compute_cap", "driver_version", "memory.total", "power.limit",
                  "power.default_limit", "power.max_limit", "pcie.link.gen.max", "pcie.link.width.max",
                  "vbios_version")


def _nvidia_smi() -> dict:
    if not shutil.which("nvidia-smi"):
        return {"error": "nvidia-smi not found"}
    rows = _run(["nvidia-smi", f"--query-gpu={','.join(_NVIDIA_FIELDS)}", "--format=csv,noheader"], timeout=30)
    if rows is None:
        return {"error": "nvidia-smi failed"}
    gpus = [dict(zip(_NVIDIA_FIELDS, (c.strip() for c in row.split(",")))) for row in rows.splitlines() if row]
    banner = _run(["nvidia-smi"], timeout=30) or ""
    m = re.search(r"CUDA Version:\s*([\d.]+)", banner)
    return {"gpus": gpus, "driver_cuda": m.group(1) if m else None}


def _probe_python(ctx: "Context") -> str:
    """The interpreter ComfyUI runs in: the live process's argv[0], else the backend venv's.
    Not /proc/<pid>/exe: a venv's python is a symlink, and the resolved binary skips the venv."""
    argv = (ctx.processes.get("comfyui") or {}).get("argv") or []
    if argv and os.path.isabs(argv[0]) and Path(argv[0]).exists():
        return argv[0]
    venv = ctx.root / "backend" / "venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def _probe_env() -> dict:
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES", "CUDA_HOME")
    return {k: os.environ[k] for k in keep if k in os.environ}


def collect_runtime(ctx: "Context") -> dict:
    out = {"os": platform.platform(), "kernel": platform.release()}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                out["distro"] = line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith(("MemTotal:", "SwapTotal:")):
                key, kb = line.split(":")[0], int(line.split()[1])
                out["ram_gb" if key == "MemTotal" else "swap_gb"] = round(kb / 1048576, 1)
    except (OSError, ValueError):
        pass
    out["nvidia"] = _nvidia_smi()
    python = _probe_python(ctx)
    raw = _run([python, "-c", _RUNTIME_PROBE, json.dumps(sorted(PACKAGES))], timeout=180, env=_probe_env())
    try:
        out["comfyui_python"] = json.loads(raw) if raw else {"error": f"probe failed ({python})"}
    except json.JSONDecodeError:
        out["comfyui_python"] = {"error": "probe printed something other than JSON"}
    return out


# ── ComfyUI ──────────────────────────────────────────────────────────────────

def _comfy_url(ctx: "Context") -> str:
    explicit = ctx.shell_env.get("GUAARDVARK_COMFYUI_URL") or ctx.dotenv.get("GUAARDVARK_COMFYUI_URL")
    if explicit:
        return explicit.rstrip("/")
    for name in ("plugin.local.json", "plugin.json"):
        try:
            port = int(json.loads((ctx.root / "plugins" / "comfyui" / name).read_text())["port"])
            return f"http://127.0.0.1:{port}"
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return "http://127.0.0.1:8188"


def _is_local(url: str) -> bool:
    return (urllib.parse.urlparse(url).hostname or "") in ("127.0.0.1", "localhost", "::1")


def _get_json(url: str, timeout: float = 15):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def _options(spec) -> list:
    """The option list of a ComfyUI input spec, in either shape it is served in."""
    if not isinstance(spec, list) or not spec:
        return []
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options") or []
    return []


def _strip_file_lists(node: dict) -> dict:
    """A node definition with model-file dropdowns emptied, so it hashes the same on every machine."""
    out = json.loads(json.dumps(node))
    suffixes = (".safetensors", ".sft", ".gguf", ".pth", ".pt", ".ckpt", ".bin", ".onnx")
    for group in ("required", "optional"):
        for name, spec in ((out.get("input") or {}).get(group) or {}).items():
            opts = _options(spec)
            if opts and any(isinstance(o, str) and o.lower().endswith(suffixes) for o in opts):
                if isinstance(spec[0], list):
                    spec[0] = []
                else:
                    spec[1]["options"] = []
    for k in ("display_name", "description", "category", "search_aliases"):
        out.pop(k, None)
    return out


def _source_has_node(comfy_dir: Path, class_type: str) -> bool:
    """Whether ComfyUI's own sources define a node class (used when ComfyUI is down)."""
    needle = f'"{class_type}"'
    for path in [comfy_dir / "nodes.py", *sorted((comfy_dir / "comfy_extras").glob("*.py"))]:
        try:
            if needle in path.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def _comfy_flags(argv: list) -> dict:
    """ComfyUI's argv as {flag: value}, and the attention backend it asks for."""
    flags, i = {}, 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("--"):
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is not None and not nxt.startswith("--"):
                flags[arg] = nxt
                i += 2
                continue
            flags[arg] = True
        i += 1
    attention = next((name for flag, name in (("--use-ck-attention", "ck"), ("--use-sage-attention", "sage"),
                                              ("--use-flash-attention", "flash"),
                                              ("--use-pytorch-cross-attention", "pytorch (explicit)"),
                                              ("--use-split-cross-attention", "split"),
                                              ("--use-quad-cross-attention", "quad"))
                      if flag in flags), "pytorch (default)")
    return {"attention": attention, "flags": flags}


_STARTUP_LINE = re.compile(
    r"Total VRAM|pytorch version|vram state|smart memory|weight offloading|attention|DynamicVRAM|"
    r"ModelPatcher|cu1[0-9]{2}|optimized CUDA|comfy_kitchen backend|Python version|ComfyUI version|\bDevice:|"
    r"version: \d|VAE dtype|fp16|bf16|fp8|pinned|xformers|torch compile|extra search path|Error|ERROR",
    re.IGNORECASE)
_RENDER_LINE = re.compile(
    r"got prompt|weight dtype|model_type|load device|Requested to load|loaded (completely|partially)|"
    r"lowvram|Prompt executed|Error|error|Traceback|\bnan\b|\bNaN\b|invalid value|Warning|WARNING|out of memory",
)
_KITCHEN = re.compile(r"Found comfy_kitchen backend (\w+): \{'available': (\w+), 'disabled': (\w+), "
                      r"'unavailable_reason': ([^,]+),")


def _tail_lines(path: Path, max_bytes: int = 6 << 20) -> list:
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read().decode("utf-8", "replace")
    return [_ANSI.sub("", ln).rstrip() for ln in data.splitlines()]


def comfy_log_summary(lines: list, width: int = 220) -> dict:
    """The last start-up's notable lines, and the lines of the last Wan render."""
    def clip(line):
        m = _KITCHEN.search(line)
        if m:
            line = (f"comfy_kitchen backend {m.group(1)}: available={m.group(2)} "
                    f"disabled={m.group(3)} reason={m.group(4)}")
        return line[:width]

    out = {"startup": [], "last_wan_render": []}
    starts = [i for i, ln in enumerate(lines) if "Total VRAM" in ln]
    if starts:
        begin = starts[-1]
        prior = [i for i in range(max(0, begin - 400), begin) if "Starting server" in lines[i]]
        begin = (prior[-1] + 1) if prior else max(0, begin - 60)
        end = next((i for i in range(starts[-1], len(lines)) if "Starting server" in lines[i]), len(lines))
        seen = set()
        for ln in lines[begin:end + 1]:
            if _STARTUP_LINE.search(ln):
                text = clip(ln)
                if text not in seen:
                    seen.add(text)
                    out["startup"].append(text)
    wan = [i for i, ln in enumerate(lines) if re.search(r"Requested to load WAN2", ln)]
    if wan:
        last = wan[-1]
        begin = next((i for i in range(last, -1, -1) if "got prompt" in lines[i]), max(0, last - 40))
        end = next((i for i in range(last, len(lines)) if "Prompt executed" in lines[i]
                    or (i > last and "got prompt" in lines[i])), min(len(lines) - 1, last + 80))
        block = [clip(ln) for ln in lines[begin:end + 1] if _RENDER_LINE.search(ln)]
        out["last_wan_render"] = block[:60]
    return out


def collect_comfyui(ctx: "Context") -> dict:
    comfy = ctx.comfy_dir
    out: dict = {"dir": str(comfy), "exists": (comfy / "main.py").is_file()}
    if not out["exists"]:
        return out
    ver = comfy / "comfyui_version.py"
    if ver.is_file():
        m = re.search(r'__version__\s*=\s*"([^"]+)"', ver.read_text())
        out["version"] = m.group(1) if m else None
    out["git"] = _git_info(comfy)
    # start.sh patches these two files at every launch (ComfyUI#13109, #15441).
    try:
        quant = (comfy / "comfy/quant_ops.py").read_text().splitlines()
        at = next((i for i, ln in enumerate(quant) if "Failed to import comfy_kitchen" in ln), None)
        guard = next((quant[i].strip() for i in range(at, -1, -1) if quant[i].lstrip().startswith("except")),
                     None) if at is not None else None
        out["start_sh_patches"] = {
            "model_patcher_patches_clear": "self.patches.clear()" in (comfy / "comfy/model_patcher.py").read_text(),
            "quant_ops_kitchen_guard": guard,
        }
    except OSError as e:
        out["start_sh_patches"] = {"error": str(e)}
    out["source_sha256"] = {rel: (_sha256(comfy / rel)[:16] if (comfy / rel).is_file() else None)
                            for rel in COMFY_SOURCES}
    extra = comfy / "extra_model_paths.yaml"
    out["extra_model_paths_yaml"] = _sha256(extra) if extra.is_file() else None

    proc = ctx.processes.get("comfyui")
    if proc:
        out["process"] = {"argv": proc.get("argv"), "started": proc.get("started"),
                          **_comfy_flags(proc.get("argv") or [])}
    else:
        out["process"] = None

    url = ctx.comfy_url
    http: dict = {"url": url}
    if not _is_local(url):
        http["skipped"] = "not a localhost URL; this script only contacts localhost"
    else:
        try:
            stats = _get_json(f"{url}/system_stats")
            system = dict(stats.get("system") or {})
            for volatile in ("ram_free",):
                system.pop(volatile, None)
            devices = [{k: v for k, v in d.items() if k not in ("vram_free", "torch_vram_free")}
                       for d in stats.get("devices") or []]
            http.update(reachable=True, system=system, devices=devices)
        except Exception as e:  # noqa: BLE001 — ComfyUI is often stopped between jobs
            http.update(reachable=False, error=str(e)[:200])
    out["http"] = http
    ctx.comfy_reachable = bool(http.get("reachable"))
    out["nodes"] = {cls: ctx.node_info(cls) for cls in COMFY_NODES}
    log = ctx.root / "logs" / "comfyui.log"
    out["log"] = comfy_log_summary(_tail_lines(log)) if log.is_file() else "no logs/comfyui.log"
    return out


def collect_custom_nodes(ctx: "Context") -> dict:
    manifest = ctx.root / "plugins" / "comfyui" / "custom_nodes.manifest"
    pins = {}
    if manifest.is_file():
        for line in manifest.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and line.count("|") >= 2:
                name, _url, sha = (p.strip() for p in line.split("|")[:3])
                pins[name] = sha
    patches = ctx.root / "plugins" / "comfyui" / "custom_nodes.patches"
    base = ctx.comfy_dir / "custom_nodes"
    installed = sorted(p.name for p in base.iterdir() if p.is_dir() and p.name != "__pycache__") \
        if base.is_dir() else []
    nodes = {}
    for name in sorted(set(installed) | set(pins)):
        path = base / name
        entry: dict = {"pinned": pins.get(name), "installed": path.is_dir()}
        if path.is_dir():
            info = _git_info(path, limit=10)
            entry["head"] = info.get("head")
            entry["modified"] = info.get("modified") or []
            if pins.get(name):
                entry["matches_pin"] = info.get("head") == pins[name]
            # install_deps.sh applies these on top of the pin, so edits are expected there.
            entry["repo_patch"] = (patches / f"{name}.patch").is_file()
        nodes[name] = entry
    return {"manifest": {"sha256": _sha256(manifest) if manifest.is_file() else None, "pins": pins},
            "nodes": nodes}


# ── The graph the builder produces ───────────────────────────────────────────

def _builder_class():
    from backend.services.comfyui_video_generator import ComfyUIVideoGenerator
    from backend.services import video_render_limits as rl

    class OfflineBuilder(ComfyUIVideoGenerator):
        """The real graph builders without the generator's start-up: no folders
        made, no GPU lock touched, nothing queued. Node availability and total
        VRAM come from the caller."""

        def __init__(self, node_available: Callable[[str], bool], total_vram_mb: int):
            self._node_available = node_available
            self._total_vram_mb = total_vram_mb

        def comfy_node_available(self, class_type: str) -> bool:
            return bool(self._node_available(class_type))

        def _text_encoder_device(self, model_key: str) -> str:
            # 0 (not None) keeps text_encoder_device off the GPU coordinator, which writes a lock file.
            return rl.text_encoder_device(model_key, int(self._total_vram_mb or 0),
                                          family=self._model_family(model_key))

    return OfflineBuilder


def build_wan5b_graph(node_available: Callable[[str], bool], total_vram_mb: int, request: dict) -> dict:
    """Run the request through the same limits and builder the render path uses
    (comfyui_video_generator.generate_video, the Wan TI2V branch)."""
    from backend.services import video_render_limits as rl
    from backend.services.user_video_models import resolve_text_encoder

    gen = _builder_class()(node_available, total_vram_mb)
    model = MODEL_ID
    family = gen._model_family(model)
    width, height = rl.resolve_canvas(model, request["width"], request["height"], request["frames"], family)
    frames = rl.resolve_frames(model, request["frames"], family)
    fps = rl.resolve_fps(model, request["fps"], family)
    cfg = rl.cfg_when_unset(model, family)
    steps = rl.resolve_steps(model, request["steps"], explicit=False, profile=None, family=family)
    te_file, te_err = resolve_text_encoder(model, None)
    workflow = gen._create_wan22_5b_workflow(
        speed_profile=None, prompt=request["prompt"], negative_prompt="", model_key=model,
        image_filename=None, sampler_profile=None, num_frames=frames, num_inference_steps=steps,
        guidance_scale=cfg if cfg is not None else rl.LEGACY_CFG, width=width, height=height,
        seed=request["seed"], fps=fps, interpolation_multiplier=request["interpolation_multiplier"],
        extra_loras=None, text_encoder=te_file,
    )
    return {
        "resolved": {"width": width, "height": height, "frames": frames, "fps": fps, "cfg": cfg,
                     "steps": steps, "text_encoder_error": str(te_err) if te_err else None},
        "launch_attention": gen._launch_attention(),
        "capabilities": rl.limits_for(model, family),
        "workflow": workflow,
    }


def summarize_graph(graph: dict) -> dict:
    """The settings that decide how a Wan 5B graph renders, pulled out for reading."""
    if not isinstance(graph, dict):
        return {}

    def first(cls):
        return next((n.get("inputs") or {} for n in graph.values()
                     if isinstance(n, dict) and n.get("class_type") == cls), None)

    out: dict = {}
    if (u := first("UNETLoader")) is not None:
        out.update(unet=u.get("unet_name"), weight_dtype=u.get("weight_dtype"))
    if (c := first("CLIPLoader")) is not None:
        out.update(clip=c.get("clip_name"), clip_type=c.get("type"), clip_device=c.get("device"))
    if (v := first("VAELoader")) is not None:
        out["vae"] = v.get("vae_name")
    if (lat := first("Wan22ImageToVideoLatent")) is not None:
        out.update(width=lat.get("width"), height=lat.get("height"), length=lat.get("length"),
                   start_image="start_image" in lat)
    if (ms := first("ModelSamplingSD3")) is not None:
        out["shift"] = ms.get("shift")
    if (ks := first("KSampler")) is not None:
        out.update({k: ks.get(k) for k in ("steps", "cfg", "sampler_name", "scheduler", "denoise", "seed")})
    for cls in ("VAEDecodeTiled", "VAEDecode"):
        if (d := first(cls)) is not None:
            out["decode"] = cls
            if cls == "VAEDecodeTiled":
                out["decode_tiles"] = {k: d.get(k) for k in ("tile_size", "overlap", "temporal_size",
                                                             "temporal_overlap")}
            break
    if (att := first("ModelAttentionBackend")) is not None:
        out["attention_pin"] = att.get("attention")
    else:
        out["attention_pin"] = None
    if (rife := first("RIFE VFI")) is not None:
        out["rife"] = {k: rife.get(k) for k in ("ckpt_name", "multiplier", "fast_mode", "ensemble", "dtype")}
    if (vhs := first("VHS_VideoCombine")) is not None:
        out["output"] = {k: vhs.get(k) for k in ("frame_rate", "format", "pix_fmt", "crf")}
    out["loras"] = [(n["inputs"].get("lora_name"), n["inputs"].get("strength_model")) for n in graph.values()
                    if isinstance(n, dict) and n.get("class_type") == "LoraLoaderModelOnly"]
    out["class_types"] = sorted({n.get("class_type") for n in graph.values() if isinstance(n, dict)})
    return out


def collect_graph(ctx: "Context") -> dict:
    # The render runs in the backend process, so its started-with values win, as
    # they do over .env there (load_dotenv with override=False).
    backend = ctx.processes.get("backend") or {}
    source = ".env and this shell"
    if isinstance(backend.get("env"), dict):
        for key, value in backend["env"].items():
            os.environ[key] = value
        source = f"backend process {backend.get('pid')}, then .env"
    built = build_wan5b_graph(ctx.node_available, ctx.total_vram_mb(), FIXED_REQUEST)
    built["request"] = {k: v for k, v in FIXED_REQUEST.items() if k != "prompt"}
    built["env_used"] = allowlisted(os.environ)
    built["env_source"] = source
    built["node_availability"] = "ComfyUI /object_info" if ctx.comfy_reachable else \
        "ComfyUI source scan (ComfyUI not reachable)"
    built["summary"] = summarize_graph(built["workflow"])
    missing = [c for c in built["summary"]["class_types"] if ctx.comfy_reachable and not ctx.node_available(c)]
    built["classes_missing_in_comfyui"] = missing
    ctx.graph_summary = built["summary"]
    return built


# ── Model files ──────────────────────────────────────────────────────────────

def safetensors_header(path: Path) -> dict:
    """Tensor count, dtypes and metadata from a .safetensors header (first bytes only)."""
    try:
        with open(path, "rb") as f:
            (length,) = struct.unpack("<Q", f.read(8))
            if length > 100 << 20:
                return {"error": f"header length {length} is not plausible"}
            header = json.loads(f.read(length))
    except (OSError, struct.error, ValueError) as e:
        return {"error": str(e)[:200]}
    meta = header.pop("__metadata__", None) or {}
    dtypes: dict = {}
    for spec in header.values():
        if isinstance(spec, dict):
            dtypes[spec.get("dtype")] = dtypes.get(spec.get("dtype"), 0) + 1
    return {"tensors": len(header), "dtypes": dict(sorted(dtypes.items())),
            "header_sha256": hashlib.sha256(json.dumps(header, sort_keys=True).encode()).hexdigest()[:16],
            "metadata": {k: (str(v)[:120]) for k, v in sorted(meta.items())}}


def describe_file(path: Path, full_hash: bool) -> dict:
    st = path.stat()
    out = {"path": str(path), "size": st.st_size, "mtime": _iso(st.st_mtime)}
    if path.is_symlink():
        out["realpath"] = str(path.resolve())
    out["head_tail_sha256"] = _head_tail_sha256(path)
    if full_hash:
        _log(f"hashing {path.name} ({st.st_size / 1e9:.1f} GB)...")
        t0 = time.time()
        out["sha256"] = _sha256(path)
        _log(f"  done in {time.time() - t0:.0f} s")
    if path.suffix == ".safetensors":
        out["safetensors"] = safetensors_header(path)
    return out


def _rife_ckpt_dir(comfy_dir: Path) -> Path:
    node = comfy_dir / "custom_nodes" / "ComfyUI-Frame-Interpolation"
    ckpts = "./ckpts"
    try:
        m = re.search(r'^ckpts_path:\s*"?([^"\n]+)"?', (node / "config.yaml").read_text(), re.MULTILINE)
        if m:
            ckpts = m.group(1).strip()
    except OSError:
        pass
    base = Path(ckpts)
    return (base if base.is_absolute() else node / base) / "rife"


def collect_models(ctx: "Context") -> dict:
    from backend.services.video_model_registry import VIDEO_MODEL_REGISTRY, comfyui_models_dir, resolve_entry_dir

    models_dir = comfyui_models_dir()
    entry = VIDEO_MODEL_REGISTRY.get(MODEL_ID) or {}
    wanted = [(MODEL_ID, entry)] + [(dep, VIDEO_MODEL_REGISTRY.get(dep) or {}) for dep in entry.get("requires", [])]
    hash_only = ctx.hash_models
    out: dict = {"models_dir": str(models_dir)}
    for mid, e in wanted:
        sub = e.get("local_subdir", "")
        for f in e.get("files", []):
            name = f["dst"]
            candidates = [models_dir / folder / name for folder in LOADER_FOLDERS.get(sub, (sub,))]
            present = [p for p in candidates if p.is_file()]
            registry_path = resolve_entry_dir(e) / name
            full = hash_only is None or mid in hash_only
            out[mid] = {
                "file": name,
                "registry_path": str(registry_path),
                "comfyui_loads": str(present[0]) if present else None,
                "shadowed": bool(present) and present[0].resolve() != registry_path.resolve(),
                "copies": [describe_file(p, full) for p in present],
            }
    rife_name = ((ctx.graph_summary or {}).get("rife") or {}).get("ckpt_name") or "rife49.pth"
    rife = _rife_ckpt_dir(ctx.comfy_dir) / rife_name
    full = hash_only is None or "rife" in hash_only
    out["rife"] = {"file": rife_name, "path": str(rife),
                   "copies": [describe_file(rife, full)] if rife.is_file() else []}
    return out


# ── The graph inside an MP4 ──────────────────────────────────────────────────

def _boxes(f, start: int, end: int):
    """(type, payload start, box end) of each ISO-BMFF box between two offsets."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        size, kind = struct.unpack(">I4s", f.read(8))
        header = 8
        if size == 1:
            (size,) = struct.unpack(">Q", f.read(8))
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield kind, pos + header, pos + size
        pos += size


def _child(f, start, end, kind):
    return next(((s, e) for k, s, e in _boxes(f, start, end) if k == kind), None)


def _meta_children_start(f, start: int) -> int:
    # ISO 'meta' is a full box (4 bytes of version and flags); QuickTime's is not.
    f.seek(start + 4)
    return start if f.read(4) == b"hdlr" else start + 4


def _ilst_text(f, start, end, kind) -> Optional[str]:
    item = _child(f, start, end, kind)
    if not item:
        return None
    data = _child(f, item[0], item[1], b"data")
    if not data:
        return None
    f.seek(data[0] + 8)  # type indicator and locale
    return f.read(data[1] - data[0] - 8).decode("utf-8", "replace")


def mp4_comment_atoms(path: Path) -> Optional[str]:
    """The comment tag of an MP4/MOV, read from its atoms: iTunes-style ilst ©cmt,
    QuickTime mdta keys, or a bare udta ©cmt."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        moov = _child(f, 0, size, b"moov")
        if not moov:
            return None
        udta = _child(f, moov[0], moov[1], b"udta")
        metas = []
        if udta:
            meta = _child(f, udta[0], udta[1], b"meta")
            if meta:
                metas.append(meta)
            cmt = _child(f, udta[0], udta[1], b"\xa9cmt")
            if cmt and not meta:
                f.seek(cmt[0])
                (length,) = struct.unpack(">H", f.read(2))
                f.seek(cmt[0] + 4)
                return f.read(length).decode("utf-8", "replace")
        meta = _child(f, moov[0], moov[1], b"meta")
        if meta:
            metas.append(meta)
        for start, end in metas:
            first = _meta_children_start(f, start)
            ilst = _child(f, first, end, b"ilst")
            if not ilst:
                continue
            text = _ilst_text(f, ilst[0], ilst[1], b"\xa9cmt")
            if text is not None:
                return text
            keys = _child(f, first, end, b"keys")
            if keys:
                f.seek(keys[0] + 4)
                (count,) = struct.unpack(">I", f.read(4))
                pos = keys[0] + 8
                for index in range(1, count + 1):
                    f.seek(pos)
                    ksize, _ns = struct.unpack(">I4s", f.read(8))
                    if ksize < 8:
                        break
                    name = f.read(ksize - 8).decode("utf-8", "replace")
                    if name in ("comment", "com.apple.quicktime.comment"):
                        return _ilst_text(f, ilst[0], ilst[1], struct.pack(">I", index))
                    pos += ksize
    return None


def _ffprobe(path: Path) -> Optional[dict]:
    if not shutil.which("ffprobe"):
        return None
    raw = _run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams",
                str(path)], timeout=60)
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


def read_mp4(path: Path, use_ffprobe: bool = True) -> dict:
    """The executed graph VideoHelperSuite embedded in a clip, with its stream facts."""
    path = Path(path)
    out: dict = {"path": str(path), "size": path.stat().st_size, "mtime": _iso(path.stat().st_mtime)}
    probe = _ffprobe(path) if use_ffprobe else None
    comment = None
    if probe:
        comment = ((probe.get("format") or {}).get("tags") or {}).get("comment")
        video = next((s for s in probe.get("streams") or [] if s.get("codec_type") == "video"), {})
        out["stream"] = {k: video.get(k) for k in ("codec_name", "width", "height", "pix_fmt", "r_frame_rate",
                                                   "nb_frames", "duration")}
        out["comment_source"] = "ffprobe"
    if comment is None:
        comment = mp4_comment_atoms(path)
        out["comment_source"] = "mp4 atoms"
    if comment is None:
        out["graph"] = None
        out["note"] = "no comment tag: not saved by VideoHelperSuite with save_metadata, or re-encoded after"
        return out
    try:
        meta = json.loads(comment)
        prompt = meta.get("prompt")
        out["graph"] = json.loads(prompt) if isinstance(prompt, str) else prompt
        out["comment_keys"] = sorted(meta)
    except (json.JSONDecodeError, AttributeError) as e:
        out["graph"] = None
        out["comment_error"] = f"{e}: {comment[:200]}"
        return out
    out["summary"] = summarize_graph(out["graph"])
    sidecar = path.with_name(path.name + ".metrics.json")
    if sidecar.is_file():
        try:
            q = json.loads(sidecar.read_text()).get("quality") or {}
            out["quality"] = {"flags": q.get("flags"), "metrics": (q.get("frames") or {}).get("metrics")}
        except (OSError, json.JSONDecodeError):
            pass
    for parent in list(path.parents)[:4]:
        batch = parent / "batch_metadata.json"
        if batch.is_file():
            try:
                params = dict(((json.loads(batch.read_text()).get("retry_data") or {}).get("params")) or {})
                params.pop("prompts", None)
                out["batch_request"] = params
            except (OSError, json.JSONDecodeError):
                pass
            break
    return out


def newest_mp4(dirs: Iterable[Path], prefix: str) -> Optional[Path]:
    best, best_mtime = None, -1.0
    for base in dirs:
        if not base.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                if name.startswith(prefix) and name.endswith(".mp4"):
                    p = Path(dirpath) / name
                    try:
                        m = p.stat().st_mtime
                    except OSError:
                        continue
                    if m > best_mtime:
                        best, best_mtime = p, m
    return best


def collect_mp4(ctx: "Context") -> dict:
    if ctx.mp4_paths:
        return {"files": [read_mp4(Path(p)) for p in ctx.mp4_paths]}
    dirs = [ctx.comfy_dir / "output"]
    try:
        from backend import config
        dirs = [Path(config.UPLOAD_DIR) / "Videos", Path(config.OUTPUT_DIR), Path(config.COMFYUI_OUTPUT_DIR)] + dirs
    except Exception as e:  # noqa: BLE001 — fall back to the ComfyUI output folder alone
        _log(f"backend.config not importable ({e}); searching ComfyUI's output folder only")
    newest = newest_mp4(dict.fromkeys(dirs), ctx.mp4_prefix)
    if not newest:
        return {"files": [], "note": f"no {ctx.mp4_prefix}*.mp4 found", "searched": [str(d) for d in dirs]}
    return {"files": [read_mp4(newest)], "searched": [str(d) for d in dirs]}


# ── Checks ───────────────────────────────────────────────────────────────────

# Graph settings that do not depend on the requested size, length or prompt.
SHAPE_KEYS = ("unet", "weight_dtype", "clip", "clip_device", "vae", "shift", "steps", "cfg", "sampler_name",
              "scheduler", "attention_pin")


def checks(fp: dict) -> list:
    notes = []
    for name, node in ((fp.get("custom_nodes") or {}).get("nodes") or {}).items():
        if node.get("pinned") and not node.get("installed"):
            notes.append(f"custom node {name} is pinned in the manifest but not installed")
        elif node.get("matches_pin") is False:
            notes.append(f"custom node {name} is at {str(node.get('head'))[:12]}, manifest pins "
                         f"{str(node.get('pinned'))[:12]}")
        if node.get("modified") and not node.get("repo_patch"):
            notes.append(f"custom node {name} has local edits: {', '.join(node['modified'][:3])}")
    comfy = fp.get("comfyui") or {}
    if comfy.get("http", {}).get("reachable") is False:
        notes.append("ComfyUI was not reachable; node checks fell back to its sources")
    patches = comfy.get("start_sh_patches") or {}
    if patches.get("model_patcher_patches_clear") is False:
        notes.append("ComfyUI's model_patcher.py lacks start.sh's patches.clear() fix (ComfyUI#13109)")
    if "ImportError" in str(patches.get("quant_ops_kitchen_guard") or ""):
        notes.append("ComfyUI's quant_ops.py still catches only ImportError around comfy_kitchen (ComfyUI#15441)")
    for label, proc in ((fp.get("env") or {}).get("processes") or {}).items():
        if proc.get("dotenv_changed_after_start"):
            notes.append(f".env changed after the {label} process started; it still runs the old values")
    for mid, model in (fp.get("models") or {}).items():
        if isinstance(model, dict) and "copies" in model:
            if not model["copies"]:
                notes.append(f"{mid}: {model.get('file')} not found where ComfyUI looks")
            elif model.get("shadowed"):
                notes.append(f"{mid}: ComfyUI loads {model['comfyui_loads']}, not the registry's "
                             f"{model.get('registry_path')}")
    built = (fp.get("graph") or {}).get("summary") or {}
    for clip in (fp.get("mp4") or {}).get("files") or []:
        summary = clip.get("summary") or {}
        if not summary:
            continue
        name = Path(clip.get("path", "")).name
        diff = [k for k in SHAPE_KEYS if built and summary.get(k) != built.get(k)]
        if diff:
            notes.append(f"{name} was rendered with a graph that differs from what this checkout builds in: "
                         + ", ".join(f"{k} {summary.get(k)!r} vs {built.get(k)!r}" for k in diff))
    return notes


# ── Collection ───────────────────────────────────────────────────────────────

class Context:
    """What the sections share: paths, the processes found, ComfyUI lookups."""

    def __init__(self, root: Path, args):
        self.root = root
        self.shell_env = allowlisted(os.environ)
        self.dotenv = parse_dotenv(root / ".env")
        comfy = self.shell_env.get("GUAARDVARK_COMFYUI_DIR") or self.dotenv.get("GUAARDVARK_COMFYUI_DIR")
        self.comfy_dir = Path(comfy) if comfy else root / "plugins" / "comfyui" / "ComfyUI"
        self.comfy_url = _comfy_url(self)
        self.processes = find_processes(root, self.comfy_dir)
        self.hash_models = None if args.hash_models is None else \
            {m.strip() for m in args.hash_models.split(",") if m.strip()}
        if args.no_hash and args.hash_models is None:
            self.hash_models = set()
        self.mp4_paths = args.mp4 or []
        self.mp4_prefix = args.mp4_prefix
        self.comfy_reachable: Optional[bool] = None
        self.graph_summary: Optional[dict] = None
        self._nodes: dict = {}
        self._vram: Optional[int] = None

    def _object_info(self, cls: str) -> Optional[dict]:
        if cls in self._nodes:
            return self._nodes[cls]
        info = None
        if self.comfy_reachable is None:
            self.comfy_reachable = False
            if _is_local(self.comfy_url):
                try:
                    _get_json(f"{self.comfy_url}/system_stats", timeout=5)
                    self.comfy_reachable = True
                except Exception:  # noqa: BLE001 — stopped between jobs is normal
                    pass
        if self.comfy_reachable:
            try:
                info = (_get_json(f"{self.comfy_url}/object_info/{urllib.parse.quote(cls)}") or {}).get(cls)
            except Exception:  # noqa: BLE001
                info = None
        self._nodes[cls] = info
        return info

    def node_available(self, cls: str) -> bool:
        if self._object_info(cls) is not None:
            return True
        return False if self.comfy_reachable else _source_has_node(self.comfy_dir, cls)

    def node_info(self, cls: str) -> dict:
        info = self._object_info(cls)
        if info is None:
            return {"present": False if self.comfy_reachable else None}
        out = {"present": True, "python_module": info.get("python_module"),
               "inputs_sha256": hashlib.sha256(json.dumps(_strip_file_lists(info), sort_keys=True)
                                               .encode()).hexdigest()[:16]}
        required = (info.get("input") or {}).get("required") or {}
        if cls == "ModelAttentionBackend":
            out["attention_options"] = _options(required.get("attention"))
        if cls == "KSampler":
            out["has_uni_pc"] = "uni_pc" in _options(required.get("sampler_name"))
        if cls == "CLIPLoader":
            out["types_include_wan"] = "wan" in _options(required.get("type"))
            out["device_options"] = _options(((info.get("input") or {}).get("optional") or {}).get("device")
                                             or required.get("device"))
        return out

    def file_visible(self, cls: str, field: str, name: str) -> Optional[bool]:
        info = self._object_info(cls)
        if info is None:
            return None
        return name in _options(((info.get("input") or {}).get("required") or {}).get(field))

    def total_vram_mb(self) -> int:
        if self._vram is None:
            raw = _run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"]) or ""
            try:
                self._vram = int(raw.splitlines()[0].strip())
            except (ValueError, IndexError):
                self._vram = 0
        return self._vram


def fingerprint(root: Path, args) -> dict:
    ctx = Context(root, args)
    wanted = [s for s in SECTIONS if not args.only or s in args.only]
    fp: dict = {"format": FORMAT, "captured": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "model": MODEL_ID, "sections": wanted}
    if {"graph", "models", "mp4"} & set(wanted) and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    collectors = {"code": collect_code, "env": collect_env, "runtime": collect_runtime,
                  "comfyui": collect_comfyui, "custom_nodes": collect_custom_nodes, "graph": collect_graph,
                  "models": collect_models, "mp4": collect_mp4}
    for name in wanted:
        _log(f"collecting {name}...")
        try:
            fp[name] = collectors[name](ctx)
        except Exception as e:  # noqa: BLE001 — one failed section must not lose the others
            fp[name] = {"error": f"{type(e).__name__}: {e}"}
    if isinstance((fp.get("graph") or {}).get("summary"), dict):
        s = fp["graph"]["summary"]
        fp["graph"]["files_visible_to_comfyui"] = {
            "unet": ctx.file_visible("UNETLoader", "unet_name", s.get("unet") or ""),
            "clip": ctx.file_visible("CLIPLoader", "clip_name", s.get("clip") or ""),
            "vae": ctx.file_visible("VAELoader", "vae_name", s.get("vae") or ""),
        }
    fp["checks"] = checks(fp)
    return Scrubber(root)(fp)


# ── Diff ─────────────────────────────────────────────────────────────────────

DIFF_IGNORED = {"captured", "pid", "started", "mtime", "sections", "dist_built"}
# Sections in the order a reader wants them: what differs in the render first.
SECTION_ORDER = {name: i for i, name in enumerate(("checks", "graph", "mp4", "env", "comfyui", "custom_nodes",
                                                    "models", "runtime", "code", "format", "model"))}
_MISSING = object()


def flatten(obj, prefix: str = "") -> dict:
    """{dotted.path: value}; lists of scalars stay whole, lists of objects are indexed."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in DIFF_IGNORED:
                continue
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list) and any(isinstance(v, (dict, list)) for v in obj):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def diff_fingerprints(a: dict, b: dict, width: int = 300) -> tuple:
    """(report text, number of differences) between two fingerprints."""
    fa, fb = flatten(a), flatten(b)

    def show(v):
        if v is _MISSING:
            return "(absent)"
        text = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
        return text if width <= 0 or len(text) <= width else text[:width] + " ..."

    lines, count, section = [], 0, None
    for key in sorted(set(fa) | set(fb), key=lambda k: (SECTION_ORDER.get(k.split(".")[0].split("[")[0], 99), k)):
        va, vb = fa.get(key, _MISSING), fb.get(key, _MISSING)
        if va == vb:
            continue
        top = key.split(".")[0].split("[")[0]
        if top != section:
            section = top
            lines.append(f"\n== {top} ==")
        lines.append(f"  {key}\n      A: {show(va)}\n      B: {show(vb)}")
        count += 1
    return "\n".join(lines).lstrip("\n"), count


# ── Command line ─────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=SCRIPT_ROOT,
                    help="checkout to fingerprint (default: the one this script is in)")
    ap.add_argument("--out", type=Path, help="write the JSON here instead of printing it")
    ap.add_argument("--only", help=f"comma-separated sections: {','.join(SECTIONS)}")
    ap.add_argument("--no-hash", action="store_true",
                    help="skip the full sha256 of model files (size, header and a head/tail hash are kept)")
    ap.add_argument("--hash-models", help="full sha256 only for these: wan22-5b,wan22-vae,wan-umt5,rife")
    ap.add_argument("--mp4", action="append", help="read the embedded graph from this clip (repeatable)")
    ap.add_argument("--mp4-prefix", default="wan22_5b_", help="file prefix of the newest clip to read")
    ap.add_argument("--diff", nargs=2, metavar=("A", "B"), type=Path, help="compare two fingerprint files")
    ap.add_argument("--full", action="store_true", help="with --diff, do not shorten long values")
    args = ap.parse_args(argv)

    if args.diff:
        a, b = (json.loads(p.read_text(encoding="utf-8")) for p in args.diff)
        print(f"A: {args.diff[0]} (captured {a.get('captured')})\nB: {args.diff[1]} (captured {b.get('captured')})\n")
        report, count = diff_fingerprints(a, b, width=0 if args.full else 300)
        print(report if count else "No differences.")
        print(f"\n{count} difference(s).")
        return 1 if count else 0

    args.only = [s.strip() for s in args.only.split(",")] if args.only else None
    unknown = [s for s in args.only or [] if s not in SECTIONS]
    if unknown:
        ap.error(f"unknown section(s): {', '.join(unknown)}")
    root = args.root.resolve()
    fp = fingerprint(root, args)
    text = json.dumps(fp, indent=1, ensure_ascii=False, default=str)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        _log(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
