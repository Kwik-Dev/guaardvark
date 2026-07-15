"""
Master-authoritative registry for Interconnector core-code sync.

The machine in node_mode=master owns the canonical tally of core system files
(path + sha256 + metadata). Clients never maintain their own registry — they
compare live local hashes against the master manifest on every check.

Storage (master only): data/interconnector/core_files_registry.json
  - data/ is excluded from sync and gitignored; never shipped to clients.
  - Refreshed whenever the master builds its file manifest (live scan).
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

REGISTRY_VERSION = 2

ACTION_CREATE = "create"    # not present locally -> must be pulled from master
ACTION_UPDATE = "update"    # local hash != master hash -> master wins on apply
ACTION_DRIFT = "drift"    # alias kept for API/UI: local exists but != master
ACTION_INSYNC = "insync"  # local matches master

AVAILABLE_ACTIONS = (ACTION_CREATE, ACTION_UPDATE, ACTION_DRIFT)


class MasterCoreFilesRegistry:
    """Master-only registry: canonical list of core system files and hashes."""

    def __init__(self, project_root):
        self.project_root = Path(project_root)
        self.dir = self.project_root / "data" / "interconnector"
        self.path = self.dir / "core_files_registry.json"
        self._data: Optional[Dict] = None

    @staticmethod
    def _empty() -> Dict:
        return {
            "version": REGISTRY_VERSION,
            "role": "master",
            "node_name": None,
            "initialized_at": None,
            "updated_at": None,
            "manifest_timestamp": None,
            "file_count": 0,
            "files": {},  # path -> {hash, size, modified_at}
        }

    def load(self) -> Dict:
        if self._data is not None:
            return self._data
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                if not isinstance(data, dict) or "files" not in data:
                    raise ValueError("missing 'files' key")
                self._data = data
            except Exception as e:
                logger.error(
                    f"[CORE_REGISTRY] Corrupt registry at {self.path}, starting fresh: {e}"
                )
                self._data = self._empty()
        else:
            self._data = self._empty()
        return self._data

    def exists(self) -> bool:
        return self.path.exists()

    def _atomic_write(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._data["updated_at"] = datetime.now().isoformat()
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True))
        os.replace(str(tmp), str(self.path))

    def refresh_from_scan(
        self,
        scanned_files: List[Dict],
        *,
        node_name: Optional[str] = None,
        manifest_timestamp: Optional[str] = None,
    ) -> int:
        """Replace registry with the latest master scan (authoritative tally)."""
        d = self.load()
        if node_name:
            d["node_name"] = node_name
        if d.get("initialized_at") is None:
            d["initialized_at"] = datetime.now().isoformat()
        if manifest_timestamp:
            d["manifest_timestamp"] = manifest_timestamp

        files = {}
        for f in scanned_files:
            path = f.get("path")
            h = f.get("hash")
            if not path or not h:
                continue
            files[path] = {
                "hash": h,
                "size": f.get("size", 0),
                "modified_at": f.get("modified_at"),
            }

        d["files"] = files
        d["file_count"] = len(files)
        self._atomic_write()
        logger.info(f"[CORE_REGISTRY] Refreshed master registry: {len(files)} core file(s)")
        return len(files)

    def summary(self) -> Dict:
        d = self.load()
        return {
            "role": "master",
            "tracked_files": d.get("file_count", len(d.get("files", {}))),
            "last_updated_at": d.get("updated_at"),
            "manifest_timestamp": d.get("manifest_timestamp"),
            "initialized_at": d.get("initialized_at"),
            "node_name": d.get("node_name"),
            "exists": self.exists(),
            "path": str(self.path),
        }


# Back-compat alias (internal callers migrating off per-client registry).
SyncRegistry = MasterCoreFilesRegistry


def classify_against_master(
    master_files: List[Dict],
    local_lookup: Dict[str, Dict],
    resolve_local: Optional[Callable[[str], Optional[Dict]]] = None,
) -> List[Dict]:
    """Classify each master path by comparing master hash (M) vs local hash (L).

    Master manifest is the only authority — no client-side registry.
      L missing           -> create
      L == M              -> insync
      L != M              -> update (includes local drift; master wins on apply)
    """
    results = []
    for mf in master_files:
        path = mf.get("path")
        m = mf.get("hash")
        lf = local_lookup.get(path)
        if lf is None and resolve_local:
            lf = resolve_local(path)
        l = lf.get("hash") if lf else None

        if lf is None:
            action = ACTION_CREATE
        elif l == m:
            action = ACTION_INSYNC
        else:
            action = ACTION_UPDATE

        results.append({
            "path": path,
            "action": action,
            "master_hash": m,
            "synced_hash": None,
            "local_hash": l,
            "size": mf.get("size", 0),
            "master_modified": mf.get("modified_at"),
            "local_modified": lf.get("modified_at") if lf else None,
        })
    return results


# Deprecated — kept as thin wrapper so stale imports fail loudly in tests only.
def classify_files(
    master_files: List[Dict],
    local_lookup: Dict[str, Dict],
    registry=None,
    resolve_local: Optional[Callable[[str], Optional[Dict]]] = None,
) -> List[Dict]:
    if registry is not None:
        logger.debug(
            "[CORE_REGISTRY] Ignoring client registry argument — master manifest is authoritative"
        )
    return classify_against_master(master_files, local_lookup, resolve_local)


def ensure_backfilled(*_args, **_kwargs) -> int:
    """No-op: clients do not maintain a sync registry."""
    return 0
