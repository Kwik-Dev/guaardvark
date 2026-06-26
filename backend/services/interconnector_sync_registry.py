"""
Per-machine, per-path "what this client has actually synced from the master" registry.

This is the REAL source of truth for the core-code sync's "is this file an update?"
question. It replaces the old DECORATIVE bookkeeping (an InterconnectorSyncHistory row
marked ["__core_system_files__"] + a data/interconnector/last_core_sync.json sidecar)
which were written on every apply but NEVER read when computing the update list — so
they could never suppress anything. The update list was therefore a blind live hash
diff every time, and convergence was only ever "hoped for", never recorded.

Design (confirmed with Dean 2026-06-26):
  - Storage: a single human-readable JSON file on the CLIENT,
    data/interconnector/synced_hashes.json. data/ is excluded from the sync itself
    (interconnector_file_sync_service.exclude_patterns) and gitignored, so the registry
    is never shipped to, nor clobbered by, the sync it tracks.
  - The MASTER is the single source of truth for core code. A file that differs is
    overwritten to match the master (remote_wins); client-side "drift" is reported for
    visibility but always resolved by restoring from the master.
  - The registry records ONLY files that genuinely converged on disk, with their real
    sha256. No fabricated/placebo "synced" marks — if a file did not actually end up
    matching the master, the registry does not claim it did.
  - Writes are atomic (temp file + os.replace) so a crash mid-write cannot corrupt it.
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

REGISTRY_VERSION = 1

# Actions a path can be classified into (see classify_files).
ACTION_CREATE = "create"    # not present locally -> must be pulled
ACTION_UPDATE = "update"    # master changed since we last synced (M != R)
ACTION_DRIFT = "drift"      # we synced it (M == R) but local was edited (L != R) -> master wins
ACTION_INSYNC = "insync"    # nothing to do

# Which actions count as "an available update" the client should pull.
AVAILABLE_ACTIONS = (ACTION_CREATE, ACTION_UPDATE, ACTION_DRIFT)


class SyncRegistry:
    """Reads/writes data/interconnector/synced_hashes.json. Per-client, per-path."""

    def __init__(self, project_root):
        self.project_root = Path(project_root)
        self.dir = self.project_root / "data" / "interconnector"
        self.path = self.dir / "synced_hashes.json"
        self._data: Optional[Dict] = None  # lazy-loaded cache

    # ---- load / persist -------------------------------------------------
    @staticmethod
    def _empty() -> Dict:
        return {
            "version": REGISTRY_VERSION,
            "node_name": None,
            "master_url": None,
            "initialized_at": None,
            "updated_at": None,
            "files": {},  # path -> {"hash", "synced_at", "master_version"}
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
                # A corrupt registry must not wedge the client. Start fresh; the next
                # check will reclassify from live hashes and re-backfill.
                logger.error(f"[SYNC_REGISTRY] Corrupt registry at {self.path}, starting fresh: {e}")
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
        os.replace(str(tmp), str(self.path))  # atomic rename on POSIX

    # ---- reads ----------------------------------------------------------
    def get_synced_hash(self, path: str) -> Optional[str]:
        entry = self.load()["files"].get(path)
        return entry.get("hash") if entry else None

    def all_paths(self) -> set:
        return set(self.load()["files"].keys())

    def summary(self) -> Dict:
        d = self.load()
        return {
            "tracked_files": len(d["files"]),
            "last_synced_at": d.get("updated_at"),
            "initialized_at": d.get("initialized_at"),
            "node_name": d.get("node_name"),
            "master_url": d.get("master_url"),
            "exists": self.exists(),
            "path": str(self.path),
        }

    # ---- writes ---------------------------------------------------------
    def record_synced_bulk(
        self,
        entries: Dict[str, Dict],
        node_name: Optional[str] = None,
        master_url: Optional[str] = None,
        master_version: Optional[str] = None,
    ) -> int:
        """Merge `entries` ({path: {"hash", "master_version"?}}) and persist atomically."""
        if not entries:
            return 0
        d = self.load()
        if node_name:
            d["node_name"] = node_name
        if master_url:
            d["master_url"] = master_url
        if d.get("initialized_at") is None:
            d["initialized_at"] = datetime.now().isoformat()
        now = datetime.now().isoformat()
        for path, info in entries.items():
            d["files"][path] = {
                "hash": info["hash"],
                "synced_at": now,
                "master_version": info.get("master_version", master_version),
            }
        self._atomic_write()
        return len(entries)

    def remove(self, paths: Iterable[str]) -> int:
        d = self.load()
        removed = 0
        for p in paths:
            if p in d["files"]:
                del d["files"][p]
                removed += 1
        if removed:
            self._atomic_write()
        return removed


def classify_files(master_files: List[Dict], local_lookup: Dict[str, Dict], registry: SyncRegistry) -> List[Dict]:
    """Three-way state machine per master path.

    M = master hash (authoritative) · R = registry synced hash · L = live local hash.

      lf is None                    -> create  (not present locally)
      R is None and L == M          -> insync  (already matches; will be backfilled)
      R is None and L != M          -> update  (never recorded synced, and differs)
      M != R                        -> update  (master moved since we last synced)
      M == R and L != R             -> drift   (local edited away from synced baseline)
      else                          -> insync

    Returns dicts with path/action/master_hash/synced_hash/local_hash/size/timestamps.
    Note: only iterates master files — deletion detection (in registry, gone from master)
    is intentionally out of scope for v1 (report-only deletes were deferred).
    """
    results = []
    for mf in master_files:
        path = mf.get("path")
        m = mf.get("hash")
        lf = local_lookup.get(path)
        l = lf.get("hash") if lf else None
        r = registry.get_synced_hash(path)

        if lf is None:
            action = ACTION_CREATE
        elif r is None:
            action = ACTION_INSYNC if l == m else ACTION_UPDATE
        elif m != r:
            action = ACTION_UPDATE
        elif l != r:
            action = ACTION_DRIFT
        else:
            action = ACTION_INSYNC

        results.append({
            "path": path,
            "action": action,
            "master_hash": m,
            "synced_hash": r,
            "local_hash": l,
            "size": mf.get("size", 0),
            "master_modified": mf.get("modified_at"),
            "local_modified": lf.get("modified_at") if lf else None,
        })
    return results


def ensure_backfilled(
    registry: SyncRegistry,
    master_files: List[Dict],
    local_lookup: Dict[str, Dict],
    node_name: Optional[str],
    master_url: Optional[str],
    master_version: Optional[str],
) -> int:
    """One-time seed for an existing/upgraded client that has no registry yet.

    Seeds R = M for every path that is ALREADY converged on disk right now (L == M).
    Paths that differ are intentionally left out -> correctly classified as
    create/update on the very first registry-aware check (no false "all synced", and
    a converged client shows 0 updates instead of a spurious full list).
    """
    if registry.exists():
        return 0
    seed = {}
    for mf in master_files:
        path = mf.get("path")
        m = mf.get("hash")
        lf = local_lookup.get(path)
        if lf and lf.get("hash") == m:
            seed[path] = {"hash": m, "master_version": master_version}
    registry.record_synced_bulk(seed, node_name=node_name, master_url=master_url, master_version=master_version)
    logger.info(f"[SYNC_REGISTRY] Backfilled {len(seed)} already-converged paths into new registry")
    return len(seed)
