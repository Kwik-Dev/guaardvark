"""Reconcile an isolated plugin venv via its scripts/setup_venv.sh.

compute_hash folds in hardware_policy.policy_fingerprint() so a hardware
change (e.g. restoring onto a different GPU) forces a rebuild, not just a
requirements change.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from scripts.dep_reconciler.base import Reconciler


class IsolatedPluginVenv(Reconciler):
    def __init__(self, repo_root: Path, plugin_id: str):
        self.root = repo_root
        self.plugin_id = plugin_id
        self.id = f"isolated_plugin_venv:{plugin_id}"
        self.name = f"Isolated plugin venv ({plugin_id})"

    def _plugin_dir(self) -> Path:
        return self.root / "plugins" / self.plugin_id

    def _setup_script(self) -> Path:
        return self._plugin_dir() / "scripts" / "setup_venv.sh"

    def manifests(self) -> list[Path]:
        d = self._plugin_dir()
        reqs = sorted(d.glob("requirements*.txt"))
        return reqs + [self._setup_script()]

    def is_active(self) -> bool:
        return self._setup_script().is_file()

    def _hardware(self) -> dict:
        from backend.services.hardware_policy import _load_hardware
        return _load_hardware()

    def compute_hash(self) -> str:
        from scripts.dep_reconciler.util import hash_file
        from backend.services import hardware_policy
        h = hashlib.sha256()
        for m in self.manifests():
            h.update((hash_file(m) or "").encode("ascii"))
            h.update(b"\n")
        h.update(hardware_policy.policy_fingerprint(self._hardware()).encode("ascii"))
        return f"sha256:{h.hexdigest()}"

    def install(self, log_path: Path) -> int:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n=== {self.id} install ===\n")
            log.flush()
            proc = subprocess.run(
                ["bash", str(self._setup_script())],
                stdout=log, stderr=subprocess.STDOUT,
            )
            if proc.returncode != 0:
                # DELIBERATE: return 0 even on a non-zero setup_venv exit.
                # A single broken plugin venv must NOT abort the whole reconcile
                # run / boot (one optional plugin failing != system down). The
                # degraded state is still surfaced two other ways: setup_venv.sh
                # logged its own "DEGRADED: ... VERIFY_FAIL" line above, and the
                # end-of-boot verify_gpu_stack.sh records it to
                # data/gpu_stack_status.json for the health layer. So we log a
                # WARN here and keep going rather than failing the reconciler.
                log.write(f"WARN: {self.id} setup_venv exited {proc.returncode} (degraded)\n")
                log.flush()
            return 0
