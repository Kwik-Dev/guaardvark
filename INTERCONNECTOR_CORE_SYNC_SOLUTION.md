# Interconnector Core System Files Sync via GUI — Solution

**Date:** 2026-06 (current session)
**Focus:** Simple GUI-driven sync of *core code and system files* (backend/, frontend/src/, scripts/, cli/, plugins/ manifests, start scripts, requirements, models.py, config.py, etc.) from MASTER to CLIENT. The goal is reliable transfer + apply + **marking as synced** so that the state persists (files on disk + metadata showing the sync succeeded) and future syncs recognize the client as up-to-date for those files.

**Out of scope for now (per user):** Syncing docs like GROK.md, CLAUDE.md, AGENTS.md, VERSION, README, etc. Those can be added to the list later.

## Problem (from investigation)
- The convenient GUI path (`ClientUpdatePanel.jsx` → "Apply updates" button) uses the streamlined `/updates/check`, `/updates/manifest`, `/updates/preview`, `/updates/apply` endpoints.
- `/updates/apply` (client) does the right thing: compares manifests (hashes), pulls content via the existing file sync machinery (`/sync/files/pull` + `InterconnectorFileSyncService.scan_files` + `apply_files_atomic`), writes files to disk with backups/rollback.
- **However**, this path did **not** create an `InterconnectorSyncHistory` record and did not reliably update `last_sync_time`. The full `/sync/manual` path did (but was entity-centric and not always used by the simple GUI flow).
- Result: files would be updated on disk (some persistence), but "not marked as synced" in history/status. Incremental logic was hacky (recency guess because "we don't track file sync separately"). Master views of client sync state didn't reflect core code updates. UI might not clearly show success for the file layer.

The "sync list" is the `default_sync_paths` in `InterconnectorFileSyncService` (used by manifest + pull). It already targets exactly the core system files.

Approval gating (`require_file_approval`) only affects the older manual path; the updates/apply path auto-applies differing files (good for "simply sync").

## Solution Implemented
1. **GUI experience remains simple and is now the recommended path for clients**:
   - On a CLIENT machine (Interconnector enabled, node_mode=client, master_url set):
     - Go to Settings → Interconnector (or the ClientUpdatePanel section).
     - It periodically calls `checkForUpdates()` (lightweight manifest compare).
     - Shows "Updates Available", counts (new/modified by backend/frontend/other), summary.
     - "Preview" loads detailed list of what will change.
     - "Apply all updates" (or selected) calls `applyUpdates([])` → server `/updates/apply`.
   - This is the "simply sync to update the core code and system files" button/flow.

2. **Core marking + persistence fixes** (the key changes):
   - In `/updates/apply` (the GUI apply path): after successful `apply_files_atomic(...)`, we now:
     - Create an `InterconnectorSyncHistory` record with a clear marker (`entities_synced = ["__core_system_files__"]`) + the file counts (processed/created/updated).
     - Best-effort update of any matching `InterconnectorNode.last_sync_time` (helps when master queries client `/sync/history`).
     - Write a persistent sidecar marker: `data/interconnector/last_core_sync.json` (timestamp + summary). This is filesystem-persisted, survives DB oddities on the client, and is trivial for operators/scripts to inspect (`cat data/interconnector/last_core_sync.json`).
   - Files are written to the real locations on disk by the existing atomic apply logic → they persist across reboots/restarts.
   - Next `checkForUpdates` / manifest compare will see matching hashes for synced files → `available: false`, count=0. The client is recognized as up-to-date for core.
   - Similar (lighter) improvement made to the full `/sync/manual` path so that when it includes files, the history also reflects core file syncs.

3. **Sync list / core files**:
   - We continue to use the existing `default_sync_paths` (and the service's exclude logic) for the updates/manifest/pull paths. This is already the curated list of core system files/directories (most of backend source + key roots, frontend/src + package files, scripts/, cli/, plugins/ (manifests), start*.sh, run_tests.py, requirements, etc.).
   - Heavy per-machine or generated content (data/, logs/, venvs, model weights, outputs/, uploads content, __pycache__, node_modules, etc.) is excluded (and the exclude matching was recently hardened).
   - No change to the list in this pass (as requested: revisit docs/other files later). If a truly critical core file is missing from the list, it can be added to `default_sync_paths` in `interconnector_file_sync_service.py`.

4. **Why this makes the sync "persist" and get marked**:
   - **Disk persistence**: `apply_files_atomic` does the actual writes (with parent dir creation, content from master, hash verification on conflicts).
   - **Metadata persistence (mark as synced)**: History row + `last_core_sync.json` marker + (where a node row exists) updated `last_sync_time`.
   - **Recognition on future syncs**: Hash-based diff in check/manifest + `since` support in the underlying file pull (when history has a recent entry) mean unchanged core files are skipped (no unnecessary transfer/overwrite).
   - **GUI feedback**: The panel already clears the "available" state on success and re-runs the check (now the check will see the updates are gone). The new history record means status pages and master "client sync history" views will show the core file sync event.
   - **No approval friction for core in this flow**: The updates/apply path deliberately does direct apply (new + modified). (The `require_file_approval` gating lives in the older manual sync path.)

## Files Changed (to deliver the solution)
- `backend/api/interconnector_api.py`:
  - `/updates/apply`: Added history record creation + persistent `last_core_sync.json` marker right after successful atomic apply (before the success response). Also added `history_recorded` hint in the response.
  - `/sync/manual`: Small enhancement to the history creation block so that when files are included, the record reflects it (combined marker in `entities_synced`).
- (No model change needed for the minimal version — we reuse the existing `InterconnectorSyncHistory` with a marker value in the JSON field + the existing count fields. A future pass can add dedicated `files_*` columns + migration if richer querying is desired.)

## How a user uses it (post-fix)
1. On CLIENT: Ensure Interconnector is enabled in client mode with correct master URL + API key.
2. Go to the Interconnector section in Settings.
3. The ClientUpdatePanel will show if core updates are available (based on the core sync list).
4. Preview (optional) to see exactly which core files differ.
5. Click apply. It will fetch the needed content, apply atomically (backups created), write the files, record the history + marker file.
6. "Updates available" clears. Last sync time / history now reflects the core file sync. Disk has the new code (persists).
7. On next check: clean (hashes match).
8. Master (when viewing nodes/clients) can pull the client's `/sync/history` and will see the entry.

## Future / Revisit Notes (as requested)
- When ready for docs etc.: Add "GROK.md", "AGENTS.md", "VERSION", "CLAUDE.md", etc. to `default_sync_paths` (or a dedicated `CORE_SYNC_PATHS` + docs profile).
- Consider a top-level "Sync Core from Master" button that explicitly calls the updates apply (even if the panel is the current home).
- If we want to separate "core code" from "full file sync" in profiles, we can expose `InterconnectorSyncProfile` more in the GUI or have the updates path honor a "core only" profile.
- Stronger per-file tracking (a small table of last-synced hash per path per node) could be added later if the JSON marker + history isn't enough for auditing.
- The existing `data/interconnector/last_core_sync.json` is a great operator hook.

## Verification steps (operator can run)
- On a client: `cat data/interconnector/last_core_sync.json` after a GUI apply.
- Check DB: `select * from interconnector_sync_history order by sync_timestamp desc limit 5;` (look for entries with `__core_system_files__`).
- GUI: Trigger check → apply → re-check (should go to available=false).
- File level: `git status` or `ls -l` on a changed core file on client should match master after sync.
- The file sync test endpoint (`/interconnector/sync/files/test` on master) still works for debugging the list.

This delivers a working, simple GUI experience for core system file sync with proper marking and persistence using the machinery that was already mostly in place.

If you want further tweaks (e.g. always force a history entry even on "no updates needed", expose the marker in the /updates/check response, label the panel more explicitly as "Core Code Sync", add a dedicated API button, etc.), let me know and we can iterate.