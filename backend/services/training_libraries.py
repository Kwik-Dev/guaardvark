"""The optional training libraries: Unsloth, TRL and Datasets.

Fine-tuning on the Training page needs them and a stock install does not carry
them. Settings > Training libraries lists them at the versions pinned here,
installs them into this backend's Python environment when a person clicks
Install, and takes them out again on Remove.

Nothing else starts either run. The routes refuse a request without a one-use
plan token that only the modal's status read hands out (issue_plan_token), and
no task, schedule or startup step calls start_install or start_remove
(backend/tests/test_training_libraries.py checks the second part).
"""
from __future__ import annotations

import importlib.metadata as metadata
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

# Versions, requirements and wheel sizes read from PyPI on 2026-10-06 (CPython
# 3.12, Linux x86_64) against the stack backend/requirements.txt installs.
# download_mb counts the library and the new packages it brings (download_includes).
LIBRARIES = (
    {
        "id": "unsloth",
        "name": "Unsloth",
        "version": "2026.10.1",
        "pinned_because": ("The first release whose transformers cap (<=5.17.0) admits the "
                           "transformers 5.16 Guaardvark installs; 2026.9.14 stops at 5.5.0."),
        "role": "Loads the base model in 4-bit and trains the LoRA adapter on the GPU, for text and vision jobs.",
        "download_mb": 78,
        "download_includes": "unsloth_zoo, bitsandbytes (43 MB), hf_transfer and five small packages",
        "companions": {"unsloth-zoo": "2026.10.1"},
    },
    {
        "id": "trl",
        "name": "TRL",
        "version": "1.13.0",
        "pinned_because": "The newest release Unsloth 2026.10.1 accepts (trl<=1.13.0).",
        "role": "The supervised fine-tuning loop (SFTTrainer) the trainer runs.",
        "download_mb": 1.4,
        "download_includes": "accelerate",
        "companions": {},
    },
    {
        "id": "datasets",
        "name": "Datasets",
        "version": "4.8.5",
        "pinned_because": "The newest release under Unsloth's <5.0.0 cap; TRL 1.13.0 needs 4.7.0 or newer.",
        "role": "Reads a job's JSONL rows into training batches.",
        "download_mb": 51,
        "download_includes": "pyarrow (50 MB), xxhash, fsspec",
        "companions": {},
    },
)

# unsloth and unsloth_zoo 2026.10.1's Linux requirements as published, less the
# pins above and LEFT_OUT. pip has no way to skip one declared dependency, so
# these install first and the Unsloth packages follow with --no-deps.
UNSLOTH_REQUIREMENTS = (
    "torch>=2.4.0,<2.15.0",
    "torchvision",
    "triton>=3.0.0; sys_platform == 'linux'",
    "transformers>=4.52.4,<=5.17.0,!=4.53.0,!=4.54.0,!=4.55.0,!=4.55.1,!=4.57.0,!=4.57.4,!=4.57.5,!=5.0.0,!=5.1.0",
    "peft>=0.18.0,!=0.11.0",
    "accelerate>=0.34.1",
    "bitsandbytes>=0.45.5,!=0.46.0,!=0.48.0",
    "huggingface_hub>=0.34.0",
    "hf_transfer",
    "diffusers",
    "sentencepiece>=0.2.0",
    "protobuf",
    "numpy",
    "tqdm",
    "psutil",
    "tyro",
    "wheel>=0.42.0",
    "packaging>=24.1",
    "typer>=0.19.0",
    "rich",
    "pydantic",
    "pyyaml",
    "nest-asyncio",
    "structlog>=24.1.0",
    "click>=8.0",
    "cut_cross_entropy",
    "msgspec",
    "regex",
    "pillow",
    "filelock",
    "requests",
    "typing_extensions",
)

# Declared by Unsloth, deliberately not installed.
LEFT_OUT = {
    "xformers": ("scripts/install_pytorch.sh removes it from this environment on every run "
                 "because it breaks diffusers imports here; Unsloth uses PyTorch attention without it."),
    "torchao": ("Unsloth needs it only for QAT and float8 export, which this trainer does not use. "
                "Once installed, peft imports it for every LoRA layer anywhere in Guaardvark, "
                "image LoRAs included."),
}

# The only installed packages an install may move; every other one is held at
# its version, so pip either adds packages or refuses. Remove puts these back.
MAY_CHANGE = {
    "accelerate": (">=1.4.0", "trl 1.13.0 needs 1.4.0 or newer (requirements.txt pins 1.2.1)"),
    "fsspec": ("<=2026.2.0", "datasets 4.8.5 needs 2026.2.0 or older"),
    "typeguard": (">=4.0.0", "tyro, which Unsloth uses, needs 4.0 or newer"),
}

NEEDS_CLICK = ("Training libraries install and uninstall only from the buttons in "
               "Settings > Training libraries. Open it there and click the button.")

_PIP_FLAGS = ("--disable-pip-version-check", "--no-input", "--progress-bar", "off")
_BACKEND_CONSTRAINTS = Path(__file__).resolve().parents[1] / "constraints.txt"
_RECORD_NAME = "guaardvark-training-libraries.json"
_TOKEN_TTL_SECONDS = 15 * 60
_MAX_TOKENS = 32
# pip prints a line per package it resolves, fetches or installs; this much
# silence means it is stuck (a dead connection), not slow.
_SILENCE_LIMIT_SECONDS = 10 * 60
_FETCH_LINE = re.compile(r"^\s*(?:Downloading|Using cached)\s+\S+\s+\(([\d.]+)\s*(kB|MB|GB)\)")
_UNIT_MB = {"kB": 1 / 1000, "MB": 1.0, "GB": 1000.0}


class Refused(Exception):
    """A request the routes answer with an error, never with a run."""

    def __init__(self, message: str, status: int = 400, code: str = "REFUSED"):
        super().__init__(message)
        self.status = status
        self.code = code


_lock = threading.Lock()
_tokens: dict[str, float] = {}
_run: dict[str, Any] = {}
_fit: dict[str, Any] | None = None


def _idle_run() -> dict[str, Any]:
    return {"state": "idle", "action": None, "phase": "", "progress": 0, "error": None,
            "started_at": None, "finished_at": None, "log": deque(maxlen=200),
            "expected_mb": 0.0, "fetched_mb": 0.0, "restart_needed": False}


_run.update(_idle_run())


# ---- what is installed ---------------------------------------------------------

def _dist_names() -> list[str]:
    names = []
    for lib in LIBRARIES:
        names.append(lib["id"])
        names.extend(lib["companions"])
    return names


def installed_versions() -> dict[str, str]:
    """Canonical distribution name -> version for this Python environment."""
    found: dict[str, str] = {}
    for dist in metadata.distributions():
        try:
            name = dist.metadata["Name"]
        except Exception:
            name = None
        if name:
            found.setdefault(canonicalize_name(name), dist.version)
    return found


def _requires(dist_name: str) -> list[str]:
    try:
        return metadata.requires(dist_name) or []
    except metadata.PackageNotFoundError:
        return []


def unmet_requirements(versions: dict[str, str]) -> list[str]:
    """What the installed training libraries declare and this environment
    lacks, LEFT_OUT excepted, in words: "trl 1.13.0 needs accelerate>=1.4.0
    (1.2.1 installed)". Empty when every one is met."""
    problems = []
    for dist_name in _dist_names():
        have_dist = versions.get(dist_name)
        if have_dist is None:
            continue
        for raw in _requires(dist_name):
            try:
                req = Requirement(raw)
            except InvalidRequirement:
                continue
            if req.marker is not None and not req.marker.evaluate({"extra": ""}):
                continue
            name = canonicalize_name(req.name)
            if name in LEFT_OUT:
                continue
            have = versions.get(name)
            if have is None:
                problems.append(f"{dist_name} {have_dist} needs {req.name}, which is not installed")
            elif req.specifier and not req.specifier.contains(have, prereleases=True):
                problems.append(f"{dist_name} {have_dist} needs {req.name}{req.specifier} ({have} installed)")
    return problems


def unavailable_reason() -> str | None:
    """Why a training job cannot run for want of the libraries, or None."""
    with _lock:
        busy = _run["state"] == "running"
    if busy:
        return ("The training libraries are being installed or removed. Create the job once "
                "Settings > Training libraries shows them installed.")
    missing = []
    for lib in LIBRARIES:
        try:
            metadata.version(lib["id"])
        except metadata.PackageNotFoundError:
            missing.append(lib["id"])
    if not missing:
        return None
    one = len(missing) == 1
    names = missing[0] if one else ", ".join(missing[:-1]) + " and " + missing[-1]
    return (f"Training needs {names}, which {'is' if one else 'are'} not installed. "
            f"Install {'it' if one else 'them'} in Settings > Training libraries.")


# ---- the hardware verdict ----------------------------------------------------------

def hardware_fit() -> dict[str, Any]:
    """hardware_policy.training_fit for this machine, read once per process."""
    global _fit
    if _fit is not None:
        return _fit
    from backend.services import hardware_policy
    try:
        hw = hardware_policy._load_hardware() or {}
    except Exception as e:
        logger.warning("Training libraries: could not read this machine's hardware: %s", e)
        return {"practical": False, "reason": f"This machine's hardware could not be read ({e}).",
                "gpu": None, "vram_mb": None, "ram_gb": None}
    gpu = hw.get("gpu", {}) or {}
    ram_gb = (hw.get("ram", {}) or {}).get("total_gb", 0)
    fit = hardware_policy.training_fit(ram_gb, gpu, hw.get("arch", ""))
    _fit = {**fit, "gpu": gpu.get("model"), "vram_mb": gpu.get("vram_mb"), "ram_gb": ram_gb}
    return _fit


# ---- the plan token ----------------------------------------------------------------

def issue_plan_token() -> str:
    """A one-use token for the next Install or Remove click, valid 15 minutes."""
    now = time.monotonic()
    token = secrets.token_urlsafe(24)
    with _lock:
        for old, expires in list(_tokens.items()):
            if expires < now:
                del _tokens[old]
        while len(_tokens) >= _MAX_TOKENS:
            del _tokens[min(_tokens, key=_tokens.get)]
        _tokens[token] = now + _TOKEN_TTL_SECONDS
    return token


def _take_token(token: Any) -> bool:
    if not isinstance(token, str) or not token:
        return False
    with _lock:
        expires = _tokens.pop(token, None)
    return expires is not None and expires >= time.monotonic()


# ---- the record of what an install changed ---------------------------------------

def _record_path() -> Path:
    return Path(sys.prefix) / _RECORD_NAME


def _read_record() -> dict[str, Any]:
    try:
        return json.loads(_record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_record(before: dict[str, str], after: dict[str, str], anyway: bool) -> None:
    """Fold one install's changes into the record Remove works from: packages
    it added, and the earlier version of each package it moved."""
    record = _read_record()
    added = dict(record.get("added", {}))
    changed = dict(record.get("changed", {}))
    for name, version in after.items():
        if name not in before:
            added[name] = version
        elif before[name] != version:
            if name in added:
                added[name] = version
            elif name in changed:
                changed[name] = {"from": changed[name]["from"], "to": version}
            else:
                changed[name] = {"from": before[name], "to": version}
    if not added and not changed and not record:
        return
    record.update({"added": added, "changed": changed, "installed_anyway": bool(anyway),
                   "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    _record_path().write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")


def removal_plan(versions: dict[str, str] | None = None) -> dict[str, Any]:
    """What Remove would do now: the packages it uninstalls and the earlier
    versions it puts back (only where nothing has moved them since)."""
    versions = installed_versions() if versions is None else versions
    record = _read_record()
    names = set(_dist_names()) | set(record.get("added", {}))
    remove = sorted(n for n in names if n in versions)
    restore = {name: change["from"] for name, change in sorted(record.get("changed", {}).items())
               if versions.get(name) == change.get("to") and name not in names}
    return {"remove": remove, "restore": restore}


# ---- status ------------------------------------------------------------------------

def _would_move(name: str, installed: str) -> bool:
    try:
        return not SpecifierSet(MAY_CHANGE[name][0]).contains(installed, prereleases=True)
    except Exception:
        return False


def _run_snapshot() -> dict[str, Any]:
    with _lock:
        run = {k: v for k, v in _run.items() if k not in ("log", "expected_mb", "fetched_mb")}
        run["log"] = list(_run["log"])[-40:]
    return run


def status(*, with_plan_token: bool = False) -> dict[str, Any]:
    versions = installed_versions()
    libraries = []
    for lib in LIBRARIES:
        have = versions.get(lib["id"])
        if have is None:
            state = "missing"
        elif have == lib["version"]:
            state = "installed"
        else:
            state = "other_version"
        libraries.append({
            "id": lib["id"], "name": lib["name"], "role": lib["role"],
            "version": lib["version"], "pinned_because": lib["pinned_because"],
            "installed_version": have, "state": state,
            "download_mb": lib["download_mb"], "download_includes": lib["download_includes"],
        })
    missing = [lib for lib in libraries if lib["state"] == "missing"]
    changes = [{"name": name, "installed": versions[name], "needs": spec, "why": why}
               for name, (spec, why) in MAY_CHANGE.items()
               if name in versions and _would_move(name, versions[name])]
    result = {
        "libraries": libraries,
        "ready": not missing,
        "download_mb": round(sum(lib["download_mb"] for lib in missing), 1),
        "unmet": unmet_requirements(versions),
        "changes": changes,
        "left_out": [{"name": name, "why": why} for name, why in LEFT_OUT.items()],
        "hardware": hardware_fit(),
        "removal": removal_plan(versions),
        "run": _run_snapshot(),
    }
    if with_plan_token:
        result["plan_token"] = issue_plan_token()
    return result


# ---- starting a run ----------------------------------------------------------------

def _claim(action: str, expected_mb: float) -> None:
    with _lock:
        if _run["state"] == "running":
            raise Refused(f"A training libraries {_run['action']} is already running.", 409, "BUSY")
        restart_needed = _run["restart_needed"]
        _run.update(_idle_run())
        _run.update({"state": "running", "action": action, "phase": "Starting", "progress": 1,
                     "started_at": time.time(), "expected_mb": max(1.0, expected_mb),
                     "restart_needed": restart_needed})


def start_install(token: Any, *, anyway: bool = False) -> dict[str, Any]:
    """Install the pinned libraries in a background thread.

    token: the plan token the modal's status read returned. anyway: the person
    chose Install anyway on a machine the hardware policy calls not practical."""
    if not _take_token(token):
        raise Refused(NEEDS_CLICK, 403, "NEEDS_CLICK")
    fit = hardware_fit()
    if not fit.get("practical") and not anyway:
        raise Refused(f"Training is not practical on this machine: {fit.get('reason')} "
                      f"Nothing was installed. Choose Install anyway to install regardless.",
                      409, "NOT_PRACTICAL")
    versions = installed_versions()
    expected = sum(lib["download_mb"] for lib in LIBRARIES if lib["id"] not in versions)
    _claim("install", expected)
    _spawn(_install, bool(anyway), name="training-libraries-install")
    return status()


def start_remove(token: Any) -> dict[str, Any]:
    """Uninstall the libraries and what their install added, in a background thread."""
    if not _take_token(token):
        raise Refused(NEEDS_CLICK, 403, "NEEDS_CLICK")
    if not removal_plan()["remove"]:
        raise Refused("The training libraries are not installed; there is nothing to remove.",
                      409, "NOTHING_TO_REMOVE")
    _claim("remove", 1.0)
    _spawn(_remove, name="training-libraries-remove")
    return status()


# ---- the runs ----------------------------------------------------------------------

def _spawn(target, *args, name: str) -> None:
    threading.Thread(target=target, args=args, daemon=True, name=name).start()


class _Failed(Exception):
    pass


def _log(line: str) -> None:
    with _lock:
        _run["log"].append(line)


def _phase(phase: str, progress: int) -> None:
    with _lock:
        _run["phase"] = phase
        _run["progress"] = max(_run["progress"], progress)
    _log(f"-- {phase}")


def _finish(state: str, error: str | None = None) -> None:
    with _lock:
        _run.update({"state": state, "error": error, "finished_at": time.time(),
                     "progress": 100 if state == "completed" else _run["progress"]})
        if state == "completed":
            _run["phase"] = "Done"
            _run["restart_needed"] = True


def _on_pip_line(line: str, *, fetching: bool) -> None:
    _log(line)
    if not fetching:
        return
    match = _FETCH_LINE.match(line)
    with _lock:
        if match:
            _run["fetched_mb"] += float(match.group(1)) * _UNIT_MB[match.group(2)]
            share = min(1.0, _run["fetched_mb"] / _run["expected_mb"])
            _run["progress"] = max(_run["progress"], 5 + int(80 * share))
        elif line.startswith("Installing collected packages"):
            _run["phase"] = "Installing collected packages"
            _run["progress"] = max(_run["progress"], 85)


def _pip(args: list[str], *, fetching: bool = False) -> int:
    """Run pip in this environment, streaming its output into the run's log."""
    env = dict(os.environ)
    # The constraints each call needs are passed on its own command line.
    env.pop("PIP_CONSTRAINT", None)
    env["PYTHONUNBUFFERED"] = "1"
    _log("$ pip " + " ".join(args))
    proc = subprocess.Popen([sys.executable, "-m", "pip", *args], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
    last_output = [time.monotonic()]
    stopped = threading.Event()

    def watchdog():
        while not stopped.wait(5):
            if time.monotonic() - last_output[0] > _SILENCE_LIMIT_SECONDS:
                _log(f"pip printed nothing for {_SILENCE_LIMIT_SECONDS // 60} minutes; stopping it.")
                proc.kill()
                return

    threading.Thread(target=watchdog, daemon=True, name="training-libraries-pip-watchdog").start()
    try:
        for line in proc.stdout:
            last_output[0] = time.monotonic()
            _on_pip_line(line.rstrip(), fetching=fetching)
        return proc.wait()
    finally:
        stopped.set()


def _hold_lines(versions: dict[str, str]) -> list[str]:
    """name==version for every installed package the install may not move."""
    free = set(_dist_names()) | set(MAY_CHANGE)
    lines = []
    for name, version in sorted(versions.items()):
        if name in free:
            continue
        try:
            Version(version)
        except InvalidVersion:
            continue
        lines.append(f"{name}=={version}")
    return lines


def install_commands(hold_file: str) -> list[list[str]]:
    """The two pip calls an install makes, in order."""
    constraints = []
    if _BACKEND_CONSTRAINTS.is_file():
        constraints += ["-c", str(_BACKEND_CONSTRAINTS)]
    constraints += ["-c", hold_file]
    first = ["install", *_PIP_FLAGS, *constraints]
    for lib in LIBRARIES:
        if lib["id"] != "unsloth":
            first.append(f"{lib['id']}=={lib['version']}")
    first.extend(UNSLOTH_REQUIREMENTS)
    unsloth = next(lib for lib in LIBRARIES if lib["id"] == "unsloth")
    second = ["install", *_PIP_FLAGS, "--no-deps", f"unsloth=={unsloth['version']}",
              *(f"{name}=={version}" for name, version in unsloth["companions"].items())]
    return [first, second]


def _install(anyway: bool) -> None:
    before = installed_versions()
    hold_path = None
    try:
        with tempfile.NamedTemporaryFile("w", prefix="guaardvark-training-hold-", suffix=".txt",
                                         delete=False, encoding="utf-8") as hold:
            hold.write("# Installed packages a training libraries install may not move.\n")
            hold.write("\n".join(_hold_lines(before)) + "\n")
            hold_path = hold.name
        first, second = install_commands(hold_path)

        _phase("Installing TRL, Datasets and Unsloth's dependencies", 5)
        code = _pip(first, fetching=True)
        if code != 0:
            raise _Failed(f"pip could not install TRL, Datasets and Unsloth's dependencies (exit {code}). "
                          f"The log shows why; packages already installed were held where they were.")
        _phase("Installing Unsloth", 90)
        code = _pip(second, fetching=True)
        if code != 0:
            raise _Failed(f"pip could not install Unsloth (exit {code}). The log shows why.")

        _phase("Checking what is installed", 97)
        after = installed_versions()
        missing = [lib["id"] for lib in LIBRARIES if lib["id"] not in after]
        if missing:
            raise _Failed(f"pip finished, but {', '.join(missing)} is still not installed.")
        unmet = unmet_requirements(after)
        if unmet:
            raise _Failed("Installed, but some requirements are not met: " + "; ".join(unmet))
        _finish("completed")
    except _Failed as e:
        _log(str(e))
        _finish("failed", str(e))
    except Exception as e:
        logger.exception("Training libraries install failed")
        _finish("failed", f"{type(e).__name__}: {e}")
    finally:
        if hold_path:
            try:
                os.unlink(hold_path)
            except OSError:
                pass
        try:
            _write_record(before, installed_versions(), anyway)
        except Exception as e:
            logger.warning("Training libraries: could not write the install record: %s", e)


def _remove() -> None:
    try:
        plan = removal_plan()
        _phase("Uninstalling " + ", ".join(plan["remove"]), 10)
        code = _pip(["uninstall", "-y", *plan["remove"]])
        if code != 0:
            raise _Failed(f"pip could not uninstall them (exit {code}). The log shows why.")
        if plan["restore"]:
            _phase("Putting back " + ", ".join(f"{n} {v}" for n, v in plan["restore"].items()), 60)
            code = _pip(["install", *_PIP_FLAGS, "--no-deps",
                         *(f"{name}=={version}" for name, version in plan["restore"].items())],
                        fetching=True)
            if code != 0:
                raise _Failed(f"The libraries are removed, but pip could not put back "
                              f"{', '.join(plan['restore'])} (exit {code}). The log shows why.")
        try:
            _record_path().unlink()
        except FileNotFoundError:
            pass
        _finish("completed")
    except _Failed as e:
        _log(str(e))
        _finish("failed", str(e))
    except Exception as e:
        logger.exception("Training libraries remove failed")
        _finish("failed", f"{type(e).__name__}: {e}")
