"""Update the bundled ComfyUI tree to a release declared in ``plugins/comfyui/releases.json``.

The tree under ``plugins/comfyui/ComfyUI`` is machine-local (gitignored and
sync-excluded), so an update is a local job: clone the declared tag next to the
live tree, move the state directories (models, custom nodes, user data,
input/output) across on the same filesystem, swap the trees, install the new
``requirements.txt`` into the backend venv, and start ComfyUI again through
the plugin system. Every step after ComfyUI is stopped is undone on failure,
and the old tree is kept as ``ComfyUI.prev-<version>`` so a working update can
still be stepped back with :meth:`ComfyUIUpdateService.restore_previous`.

Network is used only here, on the user's explicit action: ``git clone`` from
the declared GitHub repo and ``pip install`` from PyPI.

Collaborators (git/pip runner, plugin controller, HTTP probe, disk usage, the
busy probe) are injected so the whole sequence can be rehearsed against a fake
tree without touching a live ComfyUI.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

PLUGIN_ID = "comfyui"
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLUGIN_ROOT = _REPO_ROOT / "plugins" / "comfyui"
DEFAULT_PYTHON = _REPO_ROOT / "backend" / "venv" / "bin" / "python"
RELEASES_FILE = "releases.json"
LIVE_DIR = "ComfyUI"
NEXT_DIR = "ComfyUI.next"
PREV_PREFIX = "ComfyUI.prev-"
STAMP_FILE = ".requirements_installed"
LOG_BASENAME = "comfyui-update.log"
LOG_TAIL_LINES = 30

# Phase order with the percent shown when the phase begins and its plain-words
# label. ``fetch`` is update-only; ``restore_previous`` starts at ``snapshot``.
PHASES: List[tuple[str, int, str]] = [
    ("fetch", 5, "Downloading the new ComfyUI"),
    ("snapshot", 25, "Recording what is installed now"),
    ("stop", 35, "Stopping ComfyUI"),
    ("swap", 45, "Switching to the new tree"),
    ("install", 55, "Installing Python packages"),
    ("start", 80, "Starting ComfyUI"),
    ("post-check", 92, "Checking the new ComfyUI"),
    ("done", 100, "Done"),
]
_PHASE_INDEX = {name: i for i, (name, _, _) in enumerate(PHASES)}

STATES = ("idle", "running", "succeeded", "rolled_back", "failed")

# Runner results are duck-typed: anything with returncode/stdout/stderr.
Runner = Callable[..., Any]


class UpdateRefused(Exception):
    """The job was not started; the message says why (HTTP 409)."""


class UpdateError(Exception):
    """A phase failed; the job rolls back."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _version_tuple(version: Optional[str]) -> tuple:
    if not version:
        return ()
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-]", version) if p)


def read_tree_version(tree: Path) -> Optional[str]:
    """The ``__version__`` in ``<tree>/comfyui_version.py``; None if unreadable."""
    try:
        text = (tree / "comfyui_version.py").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", text)
    return match.group(1) if match else None


def requirement_names(requirements_text: str) -> List[str]:
    """Distribution names from a requirements file, comments and specifiers stripped."""
    names: List[str] = []
    for line in requirements_text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line)
        if match:
            names.append(match.group(0))
    return names


def requirements_stamp(requirements_text: bytes) -> str:
    """The stamp ``install_deps.sh`` writes: md5 of the file's bytes, newline-terminated."""
    return hashlib.md5(requirements_text).hexdigest() + "\n"


def manifest_marker_nodes(manifest_path: Path) -> List[str]:
    """Node classes named in the manifest header (``#   Node -> repo`` lines).

    A line whose left side carries a wildcard names a family, not a class, and
    is skipped; ``Node / Other`` lists are split into both names.
    """
    nodes: List[str] = []
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return nodes
    for line in lines:
        if not line.startswith("#") or "->" not in line:
            continue
        left = line.lstrip("# ").split("->", 1)[0].strip()
        if "*" in left or not left:
            continue
        for name in left.split("/"):
            name = name.strip()
            if re.fullmatch(r"[A-Za-z0-9_ ]+", name):
                nodes.append(name)
    return nodes


def _default_runner(argv, *, cwd=None, timeout=None, env=None):
    return subprocess.run(
        [str(a) for a in argv], cwd=cwd, timeout=timeout, env=env,
        capture_output=True, text=True, check=False,
    )


def _default_http_get(url: str, timeout: float):
    """``(status_code, parsed_json_or_None)``; ``(None, None)`` when unreachable."""
    import requests

    try:
        resp = requests.get(url, timeout=timeout)
    except requests.exceptions.RequestException:
        return None, None
    try:
        body = resp.json()
    except ValueError:
        body = None
    return resp.status_code, body


class PluginManagerController:
    """Start/stop ComfyUI through the plugin manager (gate, GPU exclusivity, health wait).

    A start right after a stop can be gated by the manager's cooldown; that is
    backpressure, so it is waited out and retried rather than reported as a failure.
    """

    def __init__(self, sleep: Callable[[float], None] = time.sleep, gate_retries: int = 5):
        self._sleep = sleep
        self._gate_retries = gate_retries

    @staticmethod
    def _manager():
        from backend.plugins.plugin_manager import get_plugin_manager

        return get_plugin_manager()

    def stop(self) -> Dict[str, Any]:
        return self._retry_gate(lambda: self._manager().stop_plugin(PLUGIN_ID, cancel_video_jobs=False))

    def start(self) -> Dict[str, Any]:
        return self._retry_gate(lambda: self._manager().start_plugin(PLUGIN_ID))

    def _retry_gate(self, op) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for _ in range(self._gate_retries + 1):
            result = op() or {}
            if result.get("success") or not result.get("gated"):
                return result
            self._sleep(float(result.get("cooldown_remaining") or 1.0) + 0.5)
        return result


def default_busy_reason() -> Optional[str]:
    """Active video render jobs or a job holding the GPU, in plugin_bridge's words."""
    from backend.services.plugin_bridge import _stop_blocked_reason

    return _stop_blocked_reason(PLUGIN_ID)


def default_base_url() -> str:
    try:
        from backend.plugins.plugin_manager import get_plugin_manager

        meta = get_plugin_manager().registry.get_plugin(PLUGIN_ID)
        url = meta.config.service_url if meta else None
    except Exception:
        url = None
    return (url or "http://127.0.0.1:8188").rstrip("/")


def default_log_path() -> Path:
    from backend.config import LOG_DIR

    return Path(LOG_DIR) / LOG_BASENAME


def invalidate_object_info_caches() -> None:
    """Drop the image and video generators' cached ``/object_info``: the node set changed."""
    try:
        from backend.services import comfyui_image_generator as img

        img._ENGINE_CACHE.clear()
    except Exception as e:  # noqa: BLE001
        logger.debug("image generator cache not cleared: %s", e)
    try:
        from backend.services import comfyui_video_generator as vid

        if vid._video_generator_instance is not None:
            vid._video_generator_instance._object_info_cache = None
    except Exception as e:  # noqa: BLE001
        logger.debug("video generator cache not cleared: %s", e)


# One update job per process: the status is read by the API while the job
# thread writes it, and a stale thread (an epoch behind) must never clobber a
# newer job's status.
_status_lock = threading.Lock()
_epoch = 0


def _idle_status() -> Dict[str, Any]:
    return {
        "state": "idle", "phase": None, "phase_label": None, "percent": 0,
        "from": None, "to": None, "started_at": None, "finished_at": None,
        "message": None, "log_tail": [], "epoch": _epoch,
    }


_status: Dict[str, Any] = _idle_status()
_log_tail: deque = deque(maxlen=LOG_TAIL_LINES)


def get_status() -> Dict[str, Any]:
    with _status_lock:
        snapshot = dict(_status)
    snapshot["log_tail"] = list(_log_tail)
    snapshot.pop("epoch", None)
    return snapshot


def reset_status() -> None:
    """Forget the last job (tests)."""
    global _status
    with _status_lock:
        _status = _idle_status()
        _log_tail.clear()


class ComfyUIUpdateService:
    def __init__(
        self,
        plugin_root: Optional[Path] = None,
        *,
        python: Optional[Path] = None,
        controller=None,
        base_url: Optional[str] = None,
        runner: Runner = _default_runner,
        http_get=_default_http_get,
        busy_probe: Optional[Callable[[], Optional[str]]] = None,
        disk_usage=shutil.disk_usage,
        log_path: Optional[Path] = None,
        invalidate_caches: Callable[[], None] = invalidate_object_info_caches,
        sleep: Callable[[float], None] = time.sleep,
        health_timeout_s: float = 300.0,
        health_poll_s: float = 2.0,
        clone_timeout_s: float = 900.0,
        pip_timeout_s: float = 1800.0,
    ):
        self.plugin_root = Path(plugin_root) if plugin_root else DEFAULT_PLUGIN_ROOT
        self.python = Path(python) if python else DEFAULT_PYTHON
        self._controller = controller
        self._base_url = base_url
        self.runner = runner
        self.http_get = http_get
        self._busy_probe = busy_probe
        self.disk_usage = disk_usage
        self._log_path = Path(log_path) if log_path else None
        self.invalidate_caches = invalidate_caches
        self.sleep = sleep
        self.health_timeout_s = health_timeout_s
        self.health_poll_s = health_poll_s
        self.clone_timeout_s = clone_timeout_s
        self.pip_timeout_s = pip_timeout_s

    # ── wiring ──────────────────────────────────────────────────────────

    @property
    def controller(self):
        if self._controller is None:
            self._controller = PluginManagerController(sleep=self.sleep)
        return self._controller

    @property
    def base_url(self) -> str:
        if self._base_url is None:
            self._base_url = default_base_url()
        return self._base_url.rstrip("/")

    @property
    def log_path(self) -> Path:
        if self._log_path is None:
            self._log_path = default_log_path()
        return self._log_path

    @property
    def live_dir(self) -> Path:
        return self.plugin_root / LIVE_DIR

    @property
    def next_dir(self) -> Path:
        return self.plugin_root / NEXT_DIR

    @property
    def stamp_path(self) -> Path:
        return self.plugin_root / STAMP_FILE

    # ── declared data ───────────────────────────────────────────────────

    def releases(self) -> Dict[str, Any]:
        with open(self.plugin_root / RELEASES_FILE, encoding="utf-8") as f:
            return json.load(f)

    def state_dirs(self) -> List[str]:
        return list(self.releases().get("state_dirs") or [])

    def _release(self, tag: Optional[str]) -> tuple[str, Dict[str, Any]]:
        data = self.releases()
        tag = tag or data["target"]
        rel = (data.get("releases") or {}).get(tag)
        if not rel:
            raise UpdateRefused(f"{tag} is not a declared ComfyUI release")
        return tag, rel

    def installed_version(self) -> Optional[str]:
        return read_tree_version(self.live_dir)

    def previous_tree(self) -> Optional[Path]:
        trees = sorted(p for p in self.plugin_root.glob(PREV_PREFIX + "*") if p.is_dir())
        return trees[-1] if trees else None

    def previous_version(self) -> Optional[str]:
        prev = self.previous_tree()
        if prev is None:
            return None
        return read_tree_version(prev) or prev.name[len(PREV_PREFIX):]

    # ── probes ──────────────────────────────────────────────────────────

    def busy_reason(self) -> Optional[str]:
        """Why ComfyUI must not be stopped now, or None when it is idle."""
        status, body = self.http_get(f"{self.base_url}/queue", 5)
        if status == 200 and isinstance(body, dict):
            running = len(body.get("queue_running") or [])
            pending = len(body.get("queue_pending") or [])
            if running or pending:
                return f"ComfyUI is working ({running} running, {pending} queued)"
        probe = self._busy_probe if self._busy_probe is not None else default_busy_reason
        try:
            return probe()
        except Exception as e:  # noqa: BLE001
            logger.debug("busy probe failed: %s", e)
            return None

    def free_gb(self) -> float:
        root = self.plugin_root if self.plugin_root.exists() else self.plugin_root.parent
        return round(self.disk_usage(root).free / 1e9, 1)

    def installed_packages(self, names: List[str]) -> Dict[str, Optional[str]]:
        """Installed version of each distribution in the venv, None when absent."""
        if not names:
            return {}
        script = (
            "import importlib.metadata as m, json, sys\n"
            "out = {}\n"
            "for n in sys.argv[1:]:\n"
            "    try:\n"
            "        out[n] = m.version(n)\n"
            "    except m.PackageNotFoundError:\n"
            "        out[n] = None\n"
            "print(json.dumps(out))\n"
        )
        result = self.runner([self.python, "-c", script, *names], timeout=120)
        if result.returncode != 0:
            raise UpdateError(f"could not read installed packages: {result.stderr.strip()[-400:]}")
        return json.loads(result.stdout.strip().splitlines()[-1])

    def package_changes(self, pins: Dict[str, str]) -> List[Dict[str, Optional[str]]]:
        installed = self.installed_packages(list(pins))
        return [
            {"name": name, "from": installed.get(name), "to": version}
            for name, version in pins.items()
            if installed.get(name) != version
        ]

    def check(self, target: Optional[str] = None) -> Dict[str, Any]:
        data = self.releases()
        tag, rel = self._release(target)
        installed = self.installed_version()
        available = installed is not None and _version_tuple(installed) < _version_tuple(rel["version"])
        try:
            changes = self.package_changes(rel.get("packages") or {})
        except UpdateError as e:
            logger.warning("package comparison skipped: %s", e)
            changes = []
        return {
            "installed": installed,
            "target": rel["version"],
            "target_tag": tag,
            "commit": rel.get("commit"),
            "available": available,
            "busy": self.busy_reason(),
            "disk_free_gb": self.free_gb(),
            "min_free_gb": data.get("min_free_gb", 0),
            "package_changes": changes,
            "previous": self.previous_version(),
            "keeps": self.state_dirs(),
            "download": (
                f"Downloads ComfyUI {rel['version']} from GitHub ({data['repo']}) "
                f"and its Python packages from PyPI."
            ),
            "notes": rel.get("notes"),
        }

    # ── job control ─────────────────────────────────────────────────────

    def _claim(self, old_version: Optional[str], new_version: str) -> int:
        global _status, _epoch
        with _status_lock:
            if _status.get("state") == "running":
                raise UpdateRefused("A ComfyUI update is already running")
            _epoch += 1
            _status = _idle_status()
            _status.update({
                "state": "running", "from": old_version, "to": new_version,
                "started_at": _utc_now(), "epoch": _epoch,
            })
            _log_tail.clear()
        return _epoch

    def _refuse_if_blocked(self, check: Dict[str, Any]) -> None:
        if get_status()["state"] == "running":
            raise UpdateRefused("A ComfyUI update is already running")
        if check["busy"]:
            raise UpdateRefused(f"ComfyUI is busy: {check['busy']}. Try again when it is idle.")
        if check["disk_free_gb"] < check["min_free_gb"]:
            raise UpdateRefused(
                f"Only {check['disk_free_gb']} GB free where ComfyUI lives; "
                f"the update needs at least {check['min_free_gb']} GB."
            )

    def start(self, target: Optional[str] = None, *, background: bool = True) -> Dict[str, Any]:
        """Begin the update; raises :class:`UpdateRefused` instead of starting."""
        tag, rel = self._release(target)
        check = self.check(tag)
        if check["installed"] is None:
            raise UpdateRefused("No ComfyUI tree is installed; the plugin has to be set up first.")
        if not check["available"]:
            raise UpdateRefused(f"ComfyUI {check['installed']} is already at or past {rel['version']}.")
        self._refuse_if_blocked(check)
        epoch = self._claim(check["installed"], rel["version"])
        return self._launch(
            lambda: self._run_update(tag, rel, check["installed"], epoch),
            background=background,
        )

    def restore_previous(self, *, background: bool = True) -> Dict[str, Any]:
        """Swap back to the kept ``ComfyUI.prev-*`` tree."""
        prev = self.previous_tree()
        if prev is None:
            raise UpdateRefused("There is no previous ComfyUI tree to restore.")
        installed = self.installed_version()
        check = self.check()
        self._refuse_if_blocked(check)
        prev_version = self.previous_version() or "?"
        epoch = self._claim(installed, prev_version)
        return self._launch(
            lambda: self._run_restore(prev, prev_version, installed, epoch),
            background=background,
        )

    def _launch(self, job, *, background: bool) -> Dict[str, Any]:
        if background:
            threading.Thread(target=job, name="comfyui-update", daemon=True).start()
        else:
            job()
        return get_status()

    # ── status + log ────────────────────────────────────────────────────

    def _log(self, epoch: int, line: str) -> None:
        stamped = f"{_utc_now()} {line}"
        logger.info("comfyui update: %s", line)
        with _status_lock:
            if _status.get("epoch") == epoch:
                _log_tail.append(stamped)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(stamped + "\n")
        except OSError as e:
            logger.warning("update log not written: %s", e)

    def _set(self, epoch: int, **fields) -> None:
        with _status_lock:
            if _status.get("epoch") == epoch:
                _status.update(fields)

    def _phase(self, epoch: int, name: str) -> None:
        _, percent, label = PHASES[_PHASE_INDEX[name]]
        self._set(epoch, phase=name, phase_label=label, percent=percent)
        self._log(epoch, f"[{name}] {label}")

    def _finish(self, epoch: int, state: str, message: str) -> None:
        fields: Dict[str, Any] = {"state": state, "message": message, "finished_at": _utc_now()}
        if state == "succeeded":
            fields["percent"] = 100
        self._set(epoch, **fields)
        self._log(epoch, f"{state}: {message}")

    # ── the jobs ────────────────────────────────────────────────────────

    def _run_update(self, tag: str, rel: Dict[str, Any], old_version: str, epoch: int) -> None:
        data = self.releases()
        ctx = _Transition(self, epoch, old_version=old_version, new_version=rel["version"])
        try:
            self._phase(epoch, "fetch")
            self._fetch(epoch, data["repo"], tag, rel)
            ctx.new_tree = self.next_dir
            ctx.apply(post_nodes=list(rel.get("post_start_nodes") or []))
        except Exception as e:  # noqa: BLE001
            ctx.rollback(e)

    def _run_restore(self, prev: Path, prev_version: str, old_version: Optional[str], epoch: int) -> None:
        ctx = _Transition(self, epoch, old_version=old_version, new_version=prev_version)
        ctx.new_tree = prev
        try:
            ctx.apply(post_nodes=[])
        except Exception as e:  # noqa: BLE001
            ctx.rollback(e)

    def _fetch(self, epoch: int, repo: str, tag: str, rel: Dict[str, Any]) -> None:
        nxt = self.next_dir
        if nxt.exists():
            self._log(epoch, f"removing stale {nxt.name}")
            shutil.rmtree(nxt)
        result = self.runner(
            ["git", "clone", "--depth", "1", "--branch", tag, repo, nxt],
            timeout=self.clone_timeout_s,
        )
        if result.returncode != 0:
            raise UpdateError(f"git clone failed: {(result.stderr or '').strip()[-400:]}")
        head = self.runner(["git", "-C", nxt, "rev-parse", "HEAD"], timeout=60)
        head_sha = (head.stdout or "").strip()
        if head.returncode != 0 or head_sha != rel["commit"]:
            raise UpdateError(
                f"fetched commit {head_sha[:12] or '?'} is not the declared {rel['commit'][:12]}"
            )
        for required in ("main.py", "requirements.txt", "comfy/ldm/models/autoencoder.py"):
            if not (nxt / required).is_file():
                raise UpdateError(f"fetched tree is missing {required}")
        fetched_version = read_tree_version(nxt)
        if fetched_version != rel["version"]:
            raise UpdateError(f"fetched tree reports version {fetched_version}, expected {rel['version']}")
        self._log(epoch, f"fetched {tag} at {head_sha[:12]} into {nxt.name}")

    # ── helpers used by the transition ──────────────────────────────────

    def _pip_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        constraints = _REPO_ROOT / "backend" / "constraints.txt"
        if not env.get("PIP_CONSTRAINT") and constraints.is_file():
            env["PIP_CONSTRAINT"] = str(constraints)
        return env

    def pip_install(self, args: List[Any]) -> None:
        result = self.runner(
            [self.python, "-m", "pip", "install", "--quiet", *args],
            timeout=self.pip_timeout_s, env=self._pip_env(),
        )
        if result.returncode != 0:
            raise UpdateError(f"pip install failed: {(result.stderr or result.stdout or '').strip()[-600:]}")

    def read_stamp(self) -> Optional[str]:
        try:
            return self.stamp_path.read_text(encoding="utf-8")
        except OSError:
            return None

    def write_stamp(self, content: Optional[str]) -> None:
        if content is None:
            try:
                self.stamp_path.unlink()
            except FileNotFoundError:
                pass
            return
        self.stamp_path.write_text(content, encoding="utf-8")

    def wait_healthy(self) -> None:
        deadline = time.monotonic() + self.health_timeout_s
        while True:
            status, _ = self.http_get(f"{self.base_url}/", 5)
            if status == 200:
                return
            if time.monotonic() >= deadline:
                raise UpdateError(f"ComfyUI did not answer at {self.base_url} within {int(self.health_timeout_s)} s")
            self.sleep(self.health_poll_s)

    def running_version(self) -> Optional[str]:
        status, body = self.http_get(f"{self.base_url}/system_stats", 10)
        if status != 200 or not isinstance(body, dict):
            return None
        return ((body.get("system") or {}).get("comfyui_version")) or None

    def expected_nodes(self, post_nodes: List[str]) -> List[str]:
        nodes: List[str] = []
        try:
            with open(self.plugin_root / "plugin.json", encoding="utf-8") as f:
                paths = ((json.load(f).get("instance_check") or {}).get("paths") or [])
        except (OSError, ValueError):
            paths = []
        for path in paths:
            name = str(path).rstrip("/").rsplit("/", 1)[-1]
            if name:
                nodes.append(name)
        nodes.extend(manifest_marker_nodes(self.plugin_root / "custom_nodes.manifest"))
        nodes.extend(post_nodes)
        seen: Dict[str, None] = {}
        for n in nodes:
            seen.setdefault(n, None)
        return list(seen)

    def missing_nodes(self, expected: List[str]) -> List[str]:
        status, body = self.http_get(f"{self.base_url}/object_info", 60)
        if status != 200 or not isinstance(body, dict):
            raise UpdateError("ComfyUI did not answer /object_info")
        return [n for n in expected if n not in body]


class _Transition:
    """One tree swap and everything needed to undo it.

    ``apply`` runs snapshot → stop → swap → install → start → post-check; each
    step records what it changed so ``rollback`` can reverse exactly that.
    """

    def __init__(self, svc: ComfyUIUpdateService, epoch: int, *, old_version: Optional[str], new_version: str):
        self.svc = svc
        self.epoch = epoch
        self.old_version = old_version or "unknown"
        self.new_version = new_version
        self.new_tree: Optional[Path] = None
        self.phase = "fetch"
        self.moved: List[str] = []          # state dirs now inside new_tree
        self.old_tree_at: Path = svc.live_dir  # where the old tree currently is
        self.new_tree_at: Optional[Path] = None
        self.prev_target: Optional[Path] = None
        self.stale_namesake: Optional[tuple[Path, Path]] = None  # (original, parked) kept tree of the same name
        self.stopped = False
        self.started = False
        self.pip_touched = False
        self.snapshot: Dict[str, Optional[str]] = {}
        self.stamp_before: Optional[str] = None
        self.stamp_recorded = False

    # ── forward ─────────────────────────────────────────────────────────

    def _phase(self, name: str) -> None:
        self.phase = name
        self.svc._phase(self.epoch, name)

    def _log(self, line: str) -> None:
        self.svc._log(self.epoch, line)

    def apply(self, *, post_nodes: List[str]) -> None:
        svc = self.svc
        assert self.new_tree is not None
        new_reqs = self.new_tree / "requirements.txt"

        self._phase("snapshot")
        self.snapshot = svc.installed_packages(requirement_names(new_reqs.read_text(encoding="utf-8")))
        self.stamp_before = svc.read_stamp()
        self.stamp_recorded = True
        self._log(f"recorded {len(self.snapshot)} package versions and the requirements stamp")

        self._phase("stop")
        busy = svc.busy_reason()
        if busy:
            raise UpdateError(f"ComfyUI became busy: {busy}")
        result = svc.controller.stop() or {}
        if not result.get("success"):
            raise UpdateError(f"could not stop ComfyUI: {result.get('error') or result}")
        self.stopped = True

        self._phase("swap")
        self._swap()

        self._phase("install")
        self.pip_touched = True
        svc.pip_install(["-r", svc.live_dir / "requirements.txt"])
        svc.write_stamp(requirements_stamp((svc.live_dir / "requirements.txt").read_bytes()))
        self._log("requirements installed; stamp written")

        self._phase("start")
        self.started = True
        result = svc.controller.start() or {}
        if not result.get("success"):
            raise UpdateError(f"could not start ComfyUI: {result.get('error') or result}")
        svc.wait_healthy()

        self._phase("post-check")
        running = svc.running_version()
        if running != self.new_version:
            raise UpdateError(f"the running ComfyUI reports version {running}, expected {self.new_version}")
        missing = svc.missing_nodes(svc.expected_nodes(post_nodes))
        if missing:
            raise UpdateError(f"the new ComfyUI lacks node(s): {', '.join(missing)}")
        try:
            svc.invalidate_caches()
        except Exception as e:  # noqa: BLE001
            self._log(f"object_info caches not invalidated: {e}")
        self._remove_older_prev_trees()

        self._phase("done")
        svc._finish(self.epoch, "succeeded", f"ComfyUI updated from {self.old_version} to {self.new_version}.")

    def _swap(self) -> None:
        svc = self.svc
        live, new = svc.live_dir, self.new_tree
        for name in svc.state_dirs():
            src = live / name
            if not src.exists():
                continue
            dst = new / name
            if dst.exists():
                shutil.rmtree(dst)
            os.rename(src, dst)
            self.moved.append(name)
        self._log(f"carried over: {', '.join(self.moved) or 'nothing'}")

        target = svc.plugin_root / f"{PREV_PREFIX}{self.old_version}"
        if target.exists():
            # Only ever one tree per name; the stale namesake goes after success.
            parked = target.with_name(f"{target.name}.stale-{int(time.time())}")
            os.rename(target, parked)
            self.stale_namesake = (target, parked)
        os.rename(live, target)
        self.old_tree_at = target
        self.prev_target = target
        os.rename(new, live)
        self.new_tree_at = live
        self._log(f"old tree kept as {target.name}; {new.name} is now {live.name}")

    def _remove_older_prev_trees(self) -> None:
        for tree in self.svc.plugin_root.glob(PREV_PREFIX + "*"):
            if tree.is_dir() and tree != self.prev_target:
                self._log(f"removing older kept tree {tree.name}")
                shutil.rmtree(tree, ignore_errors=True)

    # ── backward ────────────────────────────────────────────────────────

    def rollback(self, error: Exception) -> None:
        svc = self.svc
        failed_phase, reason = self.phase, str(error)
        self._log(f"FAILED at {failed_phase}: {reason}")
        self.svc._set(self.epoch, phase_label=f"Undoing: {PHASES[_PHASE_INDEX[failed_phase]][2]}")
        try:
            if self.started:
                self._log("stopping the new ComfyUI")
                svc.controller.stop()
            if self.new_tree_at is not None and self.new_tree_at == svc.live_dir:
                # The new tree sits at the live path; it returns to where it
                # came from, then the old tree returns to the live path.
                parked = self.new_tree
                if parked.exists():
                    raise UpdateError(f"{parked} exists; refusing to overwrite it")
                os.rename(self.new_tree_at, parked)
                self.new_tree_at = parked
                self._log(f"new tree parked as {parked.name}")
            if self.old_tree_at != svc.live_dir:
                os.rename(self.old_tree_at, svc.live_dir)
                self.old_tree_at = svc.live_dir
                self._log("old tree is back at ComfyUI/")
            if self.stale_namesake is not None:
                original, parked = self.stale_namesake
                if parked.exists() and not original.exists():
                    os.rename(parked, original)
                self.stale_namesake = None
            if self.moved:
                holder = self.new_tree_at or self.new_tree
                for name in self.moved:
                    src = holder / name
                    dst = svc.live_dir / name
                    if dst.exists():
                        shutil.rmtree(dst)
                    os.rename(src, dst)
                self._log(f"moved back: {', '.join(self.moved)}")
                self.moved = []
            if self.new_tree_at is not None and self.new_tree_at == svc.next_dir and failed_phase != "fetch":
                self._log(f"{svc.next_dir.name} left in place for inspection")
            elif failed_phase == "fetch" and svc.next_dir.exists():
                shutil.rmtree(svc.next_dir, ignore_errors=True)
            if self.pip_touched:
                self._restore_packages()
            if self.stamp_recorded:
                svc.write_stamp(self.stamp_before)
            if self.stopped:
                self._log(f"starting ComfyUI {self.old_version}")
                result = svc.controller.start() or {}
                if not result.get("success"):
                    raise UpdateError(f"could not start ComfyUI: {result.get('error') or result}")
                svc.wait_healthy()
        except Exception as e:  # noqa: BLE001
            self._log(f"ROLLBACK FAILED: {e}")
            svc._finish(self.epoch, "failed", self._manual_instructions(failed_phase, reason, e))
            return
        outcome = "is running again" if self.stopped else "was not touched and is still running"
        svc._finish(
            self.epoch, "rolled_back",
            f"Update failed at {failed_phase}: {reason}; ComfyUI {self.old_version} {outcome}.",
        )

    def _restore_packages(self) -> None:
        svc = self.svc
        now = svc.installed_packages(list(self.snapshot))
        downgrade = [f"{n}=={v}" for n, v in self.snapshot.items() if v and now.get(n) != v]
        added = [n for n, v in self.snapshot.items() if not v and now.get(n)]
        if downgrade:
            self._log(f"reinstalling {len(downgrade)} package(s): {' '.join(downgrade)}")
            svc.pip_install(downgrade)
        if added:
            self._log(f"left installed (were absent before): {', '.join(added)}")

    def _manual_instructions(self, failed_phase: str, reason: str, rollback_error: Exception) -> str:
        svc = self.svc
        pins = " ".join(f"{n}=={v}" for n, v in self.snapshot.items() if v)
        return (
            f"Update failed at {failed_phase}: {reason}. Rolling back also failed: {rollback_error}. "
            f"ComfyUI is stopped. Old tree: {self.old_tree_at}. New tree: {self.new_tree_at or self.new_tree}. "
            f"To recover by hand: move {', '.join(self.moved) or 'no'} state dir(s) back into the old tree, "
            f"rename the old tree to {svc.live_dir}, run "
            f"'{svc.python} -m pip install {pins}' , restore {svc.stamp_path} to its previous content "
            f"(or delete it), then start ComfyUI from the Plugins page. Log: {svc.log_path}"
        )


_service: Optional[ComfyUIUpdateService] = None


def get_update_service() -> ComfyUIUpdateService:
    global _service
    if _service is None:
        _service = ComfyUIUpdateService()
    return _service
