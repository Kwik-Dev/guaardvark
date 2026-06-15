# Dep Reconciler & Plugin Isolation Expert Report

**Date:** 2026-06-14  
**Expert Role:** Dep Reconciler & Plugin Isolation Expert  
**Scope:** Analysis per assigned tasks of `scripts/dep_reconciler/`, registry, detectors, plugin state, hardware policy integration, and isolation for plugins like `audio_foundry`, `video_editor`, `lora_trainer`.  
**Key Deliverable:** Full report + recommendations. File saved at: `/home/llamax1/LLAMAX8/regroup-reports/dep-reconciler-expert.md`

## Executive Summary

The "plugin isolation" feature (full reconciler for plugins using `setup_venv.sh` or `venv-*` dirs) described in the handoff/backup is **not present in the current tree**. 

- `scripts/dep_reconciler/reconcilers/isolated_plugin_venv.py` **does not exist**.
- `test_isolated_plugin_venv_reconciler.py` (4 tests) **does not exist** in `backend/tests/dep_reconciler/`.
- `scripts/dep_reconciler/registry.py` has `classify_plugin_venv_mode` (with isolated detection) + `enabled_plugin_ids`, but `build_active_reconcilers` (not "get_reconcilers") **does not register any `IsolatedPluginVenv`**. It only builds shared plugins into `PluginBundle` + always includes `TorchVenvDetector` (detect-only).
- `compute_hash` / `manifests` in **no current reconciler** calls `hardware_policy.policy_fingerprint(...)`. The folding is absent.
- `TorchVenvDetector` (in `detectors/torch_venv.py`) provides only **warnings** for missing venvs on isolated plugins; it never installs or tracks hashes/state.
- **Risk:** Even when `setup_venv.sh` exists (audio_foundry, lora_trainer), dep_reconciler provides **no auto-tracking, no hw-drift rebuild, no on-enable install**. Manual invocation of the shell script is the only path. Hardware policy "live" behavior (GPU swap, box restore, different compute_cap) for isolated torch venvs is **not enforced by dep_reconciler**.
- `data/plugin_state.json` matches handoff description: `audio_foundry` and `video_editor` are `user_enabled: false` (dormant). `video_editor` currently routes as **shared** (no `setup_venv.sh`; has `requirements.txt`).
- **Interaction with `user_enabled`:** Fully drives active reconcilers list (via `enabled_plugin_ids` + classify filter). Disabled = no `IsolatedPluginVenv` instance created = dormant. Enabling via UI/PluginStateStore will surface it on next `dep_reconciler` run (if registered).
- `current` vs "backup/canonical" (handoff description + partial classify already landed): **Drift/incomplete port**. Classify logic and tests are present (and correct); registration, new reconciler class, hw-fp in hash, and dedicated test are missing.

This piece is critical: it makes hardware policy "live" (not one-off manual) for plugins with heavy/transitive torch (preventing the "wrong torch" disease mentioned).

**Recommendation priority:** HIGH. Port the missing reconciler cleanly (obeying stdlib-only top-level + lazy imports), update registry, add the 4 tests, ensure `manifests`/`compute_hash` incorporate the hw fingerprint + setup script + reqs.

## Task 1: Full Reads – Backup isolated_plugin_venv.py, Current + "Backup" registry.py, Classify Logic & Reconciler Registration

### 1a. Backup `isolated_plugin_venv.py`
**Status:** Does not exist on disk. No matches for `IsolatedPluginVenv|isolated_plugin_venv` anywhere in source (confirmed via exhaustive grep).

**Inferred spec from handoff/context (canonical "backup" design):**
- New file: `scripts/dep_reconciler/reconcilers/isolated_plugin_venv.py`
- Likely a `Reconciler` subclass (see `base.py`).
- Per-plugin instance (one per enabled isolated plugin, e.g. `IsolatedPluginVenv(repo_root, plugin_id="audio_foundry")`).
- `id` probably `"isolated:audio_foundry"` or `"plugin:audio_foundry"` (to coexist with lingering `plugin:*` state entries? but context distinguishes).
- `manifests()`: Returns key files that should trigger rebuild: the plugin's `scripts/setup_venv.sh`, `requirements*.txt` (for audio_foundry: `requirements.txt` + `requirements-music.txt`), possibly `plugin.json`.
- `is_active()`: True only if the plugin dir exists + (setup_venv.sh or venv-*) + (the plugin is user_enabled? but active decision may be in registry).
- `compute_hash()`: Aggregates `hash_file` over manifests **+** `hardware_policy.policy_fingerprint(...)` (imported lazily inside the method). This makes GPU change / restore to different box (different `compute_cap`) produce a new hash → drift → re-run `setup_venv.sh`.
- `install(log_path)`: Runs the plugin's `scripts/setup_venv.sh` (or equivalent bootstrap). Captures output to log. Returns rc.
- `extra_state()`: Possibly venv health markers or torch version per venv-*, analogous to `BackendVenv`'s numpy/gpu_uuid.
- Must obey `lazy_imports` guard (no non-stdlib top-level imports; see `test_lazy_imports.py` and `base.py` docstring: "All non-stdlib imports MUST happen inside method bodies").
- Likely uses `from scripts.dep_reconciler.util import hash_file`.
- Idempotent behavior is in the `setup_venv.sh` itself (verify + rebuild on fail).

**Absence impact:** No implementation to port from; must synthesize from context + patterns in sibling reconcilers (see below).

**Absolute path that should exist:** `/home/llamax1/LLAMAX8/scripts/dep_reconciler/reconcilers/isolated_plugin_venv.py`

### 1b. Current `registry.py` (full read)
**Absolute path:** `/home/llamax1/LLAMAX8/scripts/dep_reconciler/registry.py`

```python
# (full content, key excerpts with LINE_NUMBER)
 1|"""Build the active reconciler list. Stdlib-only top imports."""
...
12|def classify_plugin_venv_mode(plugin_dir: Path) -> Literal["isolated", "shared"]:
13|    """A plugin is isolated if it has setup_venv.sh OR a venv-*/ directory.
14|
15|    Isolated plugins are detected-only by TorchVenvDetector and never
16|    have their requirements installed into the main venv.
17|    """
18|    if (plugin_dir / "scripts" / "setup_venv.sh").is_file():
19|        return "isolated"
20|    for child in plugin_dir.iterdir() if plugin_dir.is_dir() else []:
21|        if child.is_dir() and child.name.startswith("venv-"):
22|            return "isolated"
23|    return "shared"
...
50|def build_active_reconcilers(repo_root: Path) -> list["Reconciler"]:
51|    """Return [BackendVenv, Alembic, PluginBundle, Frontend, CliVenv, TorchVenvDetector] in run order.
...
57|    # Import here, not at top, to keep registry.py independently testable.
58|    from scripts.dep_reconciler.reconcilers.backend_venv import BackendVenv
59|    from scripts.dep_reconciler.reconcilers.alembic import Alembic
60|    from scripts.dep_reconciler.reconcilers.plugin_bundle import PluginBundle
61|    from scripts.dep_reconciler.reconcilers.frontend import Frontend
62|    from scripts.dep_reconciler.reconcilers.cli_venv import CliVenv
63|    from scripts.dep_reconciler.detectors.torch_venv import TorchVenvDetector
...
68|    enabled = enabled_plugin_ids(plugin_state)
69|    shared_plugins = [
70|        pid for pid in enabled
71|        if (plugins_dir / pid).is_dir()
72|        and classify_plugin_venv_mode(plugins_dir / pid) == "shared"
73|        and any((plugins_dir / pid).glob("requirements*.txt"))
74|    ]
...
78|    return [
79|        BackendVenv(repo_root),
80|        Alembic(repo_root),
81|        PluginBundle(repo_root, shared_plugins),
82|        Frontend(repo_root),
83|        CliVenv(repo_root),
84|        TorchVenvDetector(repo_root),
85|    ]
```

**Current vs "backup" (handoff canonical):**
- `classify_plugin_venv_mode` **matches** the partial described (detects `setup_venv.sh` or `venv-*`).
- References to "isolated" exist in docstring + filter (lines 15,72).
- **Missing:** No import of `IsolatedPluginVenv`, no list of isolated-enabled plugins, no `*[IsolatedPluginVenv(...) for ...]` in return. Docstring for `build_active_reconcilers` (line 51) lists only the current 6 (no isolated).
- Function name: current=`build_active_reconcilers`; handoff mentions `get_reconcilers` → naming drift / incomplete port.
- `enabled_plugin_ids` (lines 26-47) fully handles v1 `{"user_enabled": {...}}` + legacy shape. Good.
- **shared_plugins filter** (lines 69-74) explicitly excludes isolated (by classify). This is correct for the split.

**Also found:** Duplicate import path in `scripts/dep_reconciler.py` (the `-m` entrypoint) and tests.

### 1c. Current vs Backup Classify Logic & Reconciler Registration
- Classify **already landed** and tested (see Task 3).
- Registration is the gap: "from partial grep the full list in get_reconcilers does not include the Isolated lines and the import (drift or incomplete port)" — **confirmed exact**.
- In `build_active_reconcilers`, isolated plugins are **intentionally filtered out of PluginBundle** (they must never go to main venv pip).
- Once `IsolatedPluginVenv` added, registry should compute `isolated_plugins = [pid for pid in enabled if ... classify == "isolated" and has_setup_indicator]` then append `[IsolatedPluginVenv(repo_root, pid) for pid in ...]`.
- Order matters (run after shared? or before torch detector). Shell scripts inside isolated venvs often call back to `backend/venv/bin/python -m backend.services.hardware_policy`.

## Task 2: Detector Changes (torch_venv.py) + Related

**Absolute path:** `/home/llamax1/LLAMAX8/scripts/dep_reconciler/detectors/torch_venv.py`

```python
 1|"""Detect-only: warn when isolated-venv plugins haven't been bootstrapped."""
...
10|class TorchVenvDetector:
11|    id = "torch_venv_detector"
12|    name = "Isolated-venv plugin readiness check"
...
17|    def detect(self) -> list[str]:
...
26|            setup = plugin / "scripts" / "setup_venv.sh"
27|            if not setup.is_file():
28|                continue
29|            # Find any venv-* directories the plugin uses.
30|            venv_dirs = [d for d in plugin.iterdir() if d.is_dir() and d.name.startswith("venv-")]
...
32|                msg = (
33|                    f"{plugin.name} torch venv missing — run "
34|                    f"plugins/{plugin.name}/scripts/setup_venv.sh"
35|                )
```

**Analysis:**
- Pure detect-only (never subclass of `Reconciler`; special-cased in `dep_reconciler.py` lines 152, 197 and trust-on-upgrade 112).
- Scans **all** plugins (not just enabled) for `setup_venv.sh`; warns only if no `venv-*` (or incomplete `bin/python`).
- **No hash, no state tracking, no install.** Perfect complement to a full `IsolatedPluginVenv` reconciler.
- Related: Called from main `_run` always (if present in list), after installs, for warnings only (never blocks exit code).
- No "changes" needed in detector itself for the new reconciler (it stays as safety net).

**Supporting reads (patterns for new class):**
- `base.py`: `/home/llamax1/LLAMAX8/scripts/dep_reconciler/base.py` (Reconciler ABC; `manifests`, `is_active`, `compute_hash`, `install`, optional `extra_state`).
- `util.py`: `/home/llamax1/LLAMAX8/scripts/dep_reconciler/util.py` ( `hash_file`, `hash_dir` — must be used in `compute_hash`).
- All sibling reconcilers (lazy imports inside methods; e.g. `from scripts.dep_reconciler.util import hash_file` inside `compute_hash`):
  - `backend_venv.py` (has `extra_state` with gpu_uuid + numpy; calls install_pytorch.sh; manifests on requirements).
  - `plugin_bundle.py` (per-member filtering + `member_hashes()` for "plugin:xxx" state entries).
  - `cli_venv.py`, `alembic.py`, `frontend.py`.
- `dep_reconciler.py` (the main): special-cases `torch_venv_detector`; uses `compute_hash` + `extra_state` for drift; trust-on-upgrade snapshot; orphan pruning for "plugin:..." entries.
- Lazy guard test: `backend/tests/dep_reconciler/test_lazy_imports.py` — new file **must** pass (imports inside methods only; "scripts" allowed in STDLIB set).

**hardware_policy integration (for Task 5):**
- `/home/llamax1/LLAMAX8/backend/services/hardware_policy.py:103` (full `policy_fingerprint`):
  ```python
  def policy_fingerprint(hardware: dict[str, Any]) -> str:
      ...
      decisions = "|".join([f"torch={torch_channel(gpu)}", ...])
      return "hwfp:" + hashlib.sha256(...).hexdigest()[:16]
  ```
- Used by audio_foundry `setup_venv.sh` (line 17: `python -m backend.services.hardware_policy torch_channel`).
- Test: `backend/tests/test_hardware_policy.py:143` (sensitive to compute_cap, stable otherwise).
- **Currently NO dep_reconciler reconciler calls it.** BackendVenv puts only `gpu_uuid` in *extra* (not the decision fp).

## Task 3: Read test_isolated... from Backup

**Status:** `backend/tests/dep_reconciler/test_isolated_plugin_venv_reconciler.py` **does not exist**. No test files contain "isolated" (grep confirmed; only unrelated README mentions).

**Current relevant tests (that would need extension / parallel for new class):**
- `backend/tests/dep_reconciler/test_registry.py` (full; tests classify + enabled_plugin_ids, including isolated cases):
  ```python
   9|def test_classify_isolated_when_setup_venv_sh_exists(tmp_path):
  10|    ...
  13|    assert classify_plugin_venv_mode(plugin) == "isolated"
  16|def test_classify_isolated_when_venv_dir_exists(tmp_path):  # audio_foundry example
  17|    ...
  19|    assert classify_plugin_venv_mode(plugin) == "isolated"
  ```
- `test_torch_venv_detector.py` (full; uses audio_foundry + lora_trainer fixtures; asserts warnings only for missing venvs).
- `test_plugin_bundle.py`, `test_entry_point.py`, `test_e2e.py`, `test_trust_on_upgrade.py` (exercise build_active_reconcilers indirectly; state hashing; pruning of plugin: entries; trust snapshot skips detectors).
- `test_lazy_imports.py` (parametrized over **all** `*.py` under `scripts/dep_reconciler/` — adding new reconciler auto-includes it).

**Inferred 4 tests for backup test file (from handoff + patterns):**
1. ID / construction for an isolated plugin.
2. `manifests()` includes setup_venv.sh + reqs.
3. `compute_hash()` changes when hw fingerprint changes (mock hardware_policy) or when setup/reqs change.
4. `install()` invokes the setup_venv.sh (patched); or is_active false when not enabled / no indicator.
5. (Bonus) Integration via registry: enabled isolated plugin produces Isolated* in the active list returned by build_active_reconcilers.

## Task 4: Check data/plugin_state.json in Backup vs Current

**Current (live):** `/home/llamax1/LLAMAX8/data/plugin_state.json`
```json
{
  "version": 1,
  "user_enabled": {
    "audio_foundry": false,
    "comfyui": false,
    ...
    "video_editor": false,
    "lora_trainer": false
  },
  ...
  "quarantined": { ... },
  "start_failure_counts": { "video_editor": 2, ... }
}
```
- Exactly as handoff: **audio_foundry and video_editor disabled** (reconcilers dormant until `user_enabled` true).
- `lora_trainer` also false + quarantined (has `venv-torch/` + setup_venv.sh in tree).

**Backup versions:**
- No differing `plugin_state.json` found under `data/agent.BACK/`, `data/agent/`, `data/agent_backup_05-11-2026.zip` (text grep for "user_enabled" + "audio_foundry" only hit the live `data/plugin_state.json` and unrelated docs).
- `data/dep_reconciler/state-LLAMAX1.json` (current host state):
  - Has entries for shared + lingering `plugin:video_editor`, `plugin:discord` etc.
  - **No** `audio_foundry` or isolated-specific entries (expected; no isolated reconciler yet).
  - `plugin:video_editor` present despite current `user_enabled:false` (pruning is lazy / only on full runs after disable; see `dep_reconciler.py:141-147`).

**Note (per task):** "current may have local state" — yes, this is the live machine state (`state-LLAMAX1.json` + `plugin_state.json`). Backups in `data/agent.BACK` are mostly Firefox profile / agent user data, not canonical code snapshots of `scripts/`.

## Task 5: Verify that policy_fingerprint is Actually Folded into manifests() + compute_hash Only in the New Isolated Class

**Verification result: CONFIRMED ABSENT in current code; will be present ONLY in the new isolated class (per design).**

- Grep across `scripts/dep_reconciler/` (and whole workspace restricted): **zero** occurrences of `policy_fingerprint` or `hardware_policy` import inside dep_reconciler files.
- Existing `compute_hash` implementations:
  - `BackendVenv`: pure file hashes of requirements + (in extra_state only) gpu_uuid/numpy. (Torch install happens as side effect of pip + install_pytorch.sh but hash does **not** include hw decisions.)
  - `PluginBundle`: hashes of members + their reqs (shared only).
  - Others: lockfile, models.py+versions, setup.py, etc. No hw.
- `extra_state` in `BackendVenv` captures some runtime gpu but **drift is not driven by it** (drift = hash mismatch OR extra mismatch; but fp is the *decision* hash meant for torch channel).
- In `dep_reconciler.py:157`: `current = r.compute_hash()`; compared to prior.
- Audio_foundry's `setup_venv.sh` **does** consult hardware_policy (for TORCH_CHANNEL passed to install_pytorch.sh inside venv), but without a tracking reconciler, no hash in state, no auto re-run on change.
- **Only the new `IsolatedPluginVenv`** (per handoff) will do:
  ```python
  # inside compute_hash (lazy)
  from backend.services import hardware_policy as hp
  hw = hp._load_hardware()  # or equivalent
  fp = hp.policy_fingerprint(hw)
  h.update(fp.encode())
  ```
  + manifests will include the per-plugin setup_venv.sh + reqs so that both code change *and* hw change trigger.

This is the "key enabler" that makes hw policy live for plugins.

## Task 6: Assess Risks (Without the Reconciler Registered)

**High risk if not ported/registered:**

1. **No auto-rebuild on hardware change:** GPU swap, different box restore, compute_cap update in hardware.json → `policy_fingerprint` changes → isolated venvs (audio_foundry/venv, venv-music, lora_trainer/venv-torch) may silently have wrong torch transitive (the "original disease"). Manual re-run of `setup_venv.sh` required. Detector will eventually warn if venvs go missing, but not on "stale but present" torch mismatch.
2. **On first enable (user_enabled flip):** Even with `setup_venv.sh` present + `classify=="isolated"`, `build_active_reconcilers` will **never** include a reconciler for it. `dep_reconciler` (run on boot/setup) does nothing. User must manually execute the script. "Reconcilers stay ready" (per handoff) only in the sense of code existence + classify, not active registration.
3. **Drift on requirements or setup_venv.sh edits:** Changes to `plugins/audio_foundry/requirements*.txt` or the script itself are invisible to dep_reconciler (no manifests tracked, no state entry "isolated:audio_foundry" or "plugin:audio_foundry").
4. **State inconsistency:** `data/dep_reconciler/state-*.json` will never have entries for isolated plugins. Orphan pruning, trust-on-upgrade snapshot, per-run dirty/install logic, and `--only` targeting all skip them. Lingering `plugin:video_editor` (shared) shows the mechanism works for the other side.
5. **TorchVenvDetector limitations:** Only logs warnings (at WARNING); never fails the reconciler run (exit code stays 0). No repair action.
6. **Plugin enable/disable flow (start.sh / PluginManager):** UI toggle writes `plugin_state.json` → next boot may run dep_reconciler but it won't act on the isolated case.
7. **Cross-machine / restore:** The whole point of folding hw fp (mentioned in handoff + hardware_policy comments) is defeated.
8. **Lora_trainer + audio_foundry + future (video_editor if it gains setup_venv.sh):** All affected. Video_editor currently safe as shared (no torch isolation).

**Mitigations if delayed:** Keep manual scripts documented (they already are, with comments referencing `classify_plugin_venv_mode`). Detector provides partial visibility. But "this piece is what makes the hardware policy 'live'" — without it, isolation is half-baked.

## Task 7: Note Interaction with plugin_state "user_enabled"

- **Single source:** `data/plugin_state.json` (v1 schema owned by `backend/plugins/plugin_state_store.py: PluginStateStore.set_user_enabled` / `get_user_enabled`; also legacy paths).
- **Consumed by dep:** `registry.enabled_plugin_ids(plugin_state_path)` (handles both shapes) → filters `shared_plugins` and (future) isolated list.
- **Effect on isolated reconcilers:** `IsolatedPluginVenv` instances are created **only** for `pid in enabled` + classify=="isolated". When `user_enabled[pid]=false` (default for audio_foundry/video_editor per current state + handoff), the class is **never instantiated** in the list returned to `_run`. Dormant = no drift check, no install, no state entry.
- **Enable flow:** User toggles in UI (writes via PluginStateStore or direct) → next `python -m scripts.dep_reconciler` (or start.sh flows) → `build_active_reconcilers` sees it → creates + runs `Isolated...` if hash differs (first time or hw change).
- **Quarantine / failure counts / running:** In same json but **ignored by dep registry** (used by `PluginManager`, `brain_state`, start logic, etc.). Dep focuses purely on user_enabled + manifest presence.
- **Pruning interaction:** "plugin:xxx" state entries for *formerly* shared plugins can linger (or get pruned); isolated will use analogous keys (likely "isolated:xxx" or "plugin:xxx" — design choice). Trust-on-upgrade snapshots skip the detector but would snapshot isolated once registered.
- **Plugin.json vs state:** Static `default_enabled` in `plugin.json` (audio_foundry true, video_editor true) is **overridden** by runtime `user_enabled` in state (see CAPABILITIES.md, plugin_registry, start.sh).
- Current reality: audio_foundry disabled in state → no shared_bundle inclusion (correctly, via classify) → no isolated (missing).

This separation keeps static manifests (git) vs per-machine runtime state (gitignored).

## Additional Observations from Multi-File Analysis

- **Current isolated plugins in tree:**
  - `plugins/audio_foundry/`: `scripts/setup_venv.sh` (builds `venv/` + `venv-music/`; consults hw_policy; heavy torch pins + post-fixups), `requirements.txt` + `requirements-music.txt`, `plugin.json` (gpu/cuda:true).
  - `plugins/lora_trainer/`: `scripts/setup_venv.sh` (for `venv-torch/`), `requirements-torch.txt`, `plugin.json`.
  - `plugins/video_editor/`: No `setup_venv.sh`; has `requirements.txt` (librosa/numpy/auto-editor, no torch); `plugin.json` (gpu:false). Routes shared today. Context may intend future isolation or was example.
- **Dep state (LLAMAX1):** Tracks `plugin:video_editor` etc. from prior enabled periods; no isolated.
- **Lazy + stdlib discipline:** Strong (enforced by AST test on every py in package). New file must import `hardware_policy` (and subprocess etc.) inside `compute_hash`/`install`/`is_active` etc.
- **Hash stability:** `util.hash_file` / `hash_dir` used everywhere; policy fp must be appended inside `compute_hash` (not manifests, since fp is runtime).
- **Invocation:** `scripts/dep_reconciler.py` (shebang + `-m`); called from start.sh (partial --only), boot flows, manual.
- **No MCP/redis involvement** for this (dep state is file-based under `data/dep_reconciler/`; redis MCP tools unrelated here).

## Recommendations

1. **Port the reconciler cleanly (highest priority):**
   - Create `/home/llamax1/LLAMAX8/scripts/dep_reconciler/reconcilers/isolated_plugin_venv.py` modeled exactly on `plugin_bundle.py` + `backend_venv.py`.
   - Class: `class IsolatedPluginVenv(Reconciler): id = "isolated:..."` (or decide on "plugin:" prefix for state compatibility).
   - Constructor: `def __init__(self, repo_root: Path, plugin_id: str): ...`
   - `manifests(self)`: `[plugin_dir/scripts/setup_venv.sh, plugin_dir/requirements*.txt, ...]`
   - `is_active(self)`: `bool(setup or venv-*) and plugin_dir.exists()`
   - `compute_hash(self)`: file hashes + `fp = hardware_policy.policy_fingerprint(loaded_hw); h.update(fp.encode())`
   - `install(self, log_path)`: `subprocess` the `bash .../setup_venv.sh` (log to provided path); return rc.
   - `extra_state`: optional per-venv python/torch presence.
   - **Lazy imports only.** Add `from __future__ import annotations`; no top-level backend.* or hashlib if avoidable inside methods.
   - Update `reconcilers/__init__.py`? (currently minimal).

2. **Update registry.py to match backup canonical:**
   - Add lazy import: `from scripts.dep_reconciler.reconcilers.isolated_plugin_venv import IsolatedPluginVenv`
   - After shared_plugins computation:
     ```python
     isolated_plugins = [
         pid for pid in enabled
         if (plugins_dir / pid).is_dir()
         and classify_plugin_venv_mode(plugins_dir / pid) == "isolated"
     ]
     ```
   - In return list: `... , *[IsolatedPluginVenv(repo_root, pid) for pid in isolated_plugins], TorchVenvDetector(...)`
   - Update docstring.
   - Consider whether isolated use "isolated:pid" or "plugin:pid" keys in state (to avoid collision with old shared entries for same pid). PluginBundle has special `member_hashes` + pruning logic; may need analogous for isolated or unify.
   - Rename `build_active_reconcilers` → `get_reconcilers`? (or keep and update handoff docs; current callers use the build name).

3. **Add the test:** Create `backend/tests/dep_reconciler/test_isolated_plugin_venv_reconciler.py` with (at minimum) the 4 tests described. Use tmp_path fixtures like `test_torch_venv_detector.py` (create setup_venv.sh + fake venv-dirs + reqs). Mock `hardware_policy` for fp sensitivity. Test via registry that enabled isolated appear in active list. Ensure passes `test_lazy_imports`.

4. **Data / state considerations:**
   - On first registration, expect drift for any currently-"manually" setup isolated plugins (their state entries will be new).
   - May want migration note or trust-on-upgrade extension for isolated.
   - Update `data/dep_reconciler/state-LLAMAX1.json`? No — let reconciler manage.
   - If video_editor ever gets `setup_venv.sh`, it will auto-switch from shared to isolated (classify will exclude from bundle; new reconciler will pick up).

5. **Other polish / hardening:**
   - Ensure `TorchVenvDetector` continues to warn even for "stale hash but present venv" (or enhance later).
   - Document in `CAPABILITIES.md` or `start.sh` the new auto behavior for isolated.
   - Add to `test_entry_point.py` / `test_e2e.py` cases exercising isolated (with setup script present).
   - In `plugin_bundle.py` style, perhaps expose `member_hashes`-like for isolated if per-plugin state entries desired.
   - Verify end-to-end: flip `user_enabled` for audio_foundry in a test plugin_state, run reconciler, assert "isolated:audio_foundry" in state + setup was "run".
   - Watch for hw fp changes triggering extra drift (add to `test_hardware_policy` if needed, or new dep test).

6. **Port order (safe, minimal blast):**
   - Write the new reconciler file + unit test.
   - Edit registry.py (add import + list logic).
   - Run `python -m pytest backend/tests/dep_reconciler/ -k "registry or torch or lazy or isolated" --tb=line`.
   - Run full `python -m scripts.dep_reconciler --dry-run --repo-root .` (or with test state).
   - Update any docs referencing "detected-only".
   - Then enable audio_foundry temporarily in test state to validate.

## Absolute File Paths Referenced (Key Ones)

- `/home/llamax1/LLAMAX8/scripts/dep_reconciler/registry.py`
- `/home/llamax1/LLAMAX8/scripts/dep_reconciler/detectors/torch_venv.py`
- `/home/llamax1/LLAMAX8/scripts/dep_reconciler/base.py`
- `/home/llamax1/LLAMAX8/scripts/dep_reconciler/util.py`
- `/home/llamax1/LLAMAX8/scripts/dep_reconciler/state.py`
- `/home/llamax1/LLAMAX8/scripts/dep_reconciler.py` (entrypoint)
- `/home/llamax1/LLAMAX8/scripts/dep_reconciler/reconcilers/*.py` (all 5 current)
- `/home/llamax1/LLAMAX8/data/plugin_state.json`
- `/home/llamax1/LLAMAX8/data/dep_reconciler/state-LLAMAX1.json`
- `/home/llamax1/LLAMAX8/backend/services/hardware_policy.py`
- `/home/llamax1/LLAMAX8/plugins/audio_foundry/scripts/setup_venv.sh`
- `/home/llamax1/LLAMAX8/plugins/audio_foundry/plugin.json`
- `/home/llamax1/LLAMAX8/plugins/video_editor/requirements.txt` + `plugin.json`
- `/home/llamax1/LLAMAX8/backend/tests/dep_reconciler/test_registry.py`
- `/home/llamax1/LLAMAX8/backend/tests/dep_reconciler/test_torch_venv_detector.py`
- `/home/llamax1/LLAMAX8/backend/tests/dep_reconciler/test_lazy_imports.py`
- `/home/llamax1/LLAMAX8/backend/plugins/plugin_state_store.py`
- `/home/llamax1/LLAMAX8/regroup-reports/dep-reconciler-expert.md` (this file)

## Conclusion

The missing `IsolatedPluginVenv` + registry registration is the gap preventing hardware-aware, dep_reconciler-driven lifecycle for isolated plugin venvs. Classify + detector + state reading are already solid. Port is straightforward following existing patterns (lazy imports critical). Once done, enabling `audio_foundry` (or future video_editor isolation) + hw changes will automatically keep venvs correct via tracked hashes including `policy_fingerprint`.

**Next actions for team:** Implement per recommendations #1-3 first. Re-run dep_reconciler and relevant tests after. This closes the loop on "prevent the original disease where plugin venvs got wrong torch transitive."

Report complete. All tasks addressed via direct file reads (list_dir, read_file, grep), analysis, and this write. No files created beyond this required report.