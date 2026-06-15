# Tests, Verification & Quality Analyst Report

**Date:** 2026-06-14  
**Workspace:** /home/llamax1/LLAMAX8  
**Analyst Role:** Tests, Verification & Quality Analyst  
**Source:** SESSION_HANDOFF_06-14-2026.md (from backups/) + direct file inspection + backup/current diff analysis

## Executive Summary

The session handoff claimed:
- 26 tests in `test_hardware_policy.py`
- 9 tests in `test_hardware_detector.py` (updated)
- 4 tests in new `test_isolated_plugin_venv_reconciler.py`
- dep_reconciler tests (91 passed)
- All green at end of session.
- Advisory `scripts/verify_gpu_stack.sh` that writes `data/gpu_stack_status.json` and always exits 0.

**Current state vs handoff expectations: Significant test + verification drift. The full test suite + verify logic that provided confidence is NOT present in the working tree.**

- Hardware policy tests: 24 present (vs 26 claimed; missing 2 VULKAN assertions).
- Hardware detector tests: 9 present (matches).
- Isolated plugin venv reconciler test: **0 present** (4 missing entirely).
- `verify_gpu_stack.sh`: **missing entirely** (no script, no `data/gpu_stack_status.json` logic or file).
- `scripts/dep_reconciler/reconcilers/isolated_plugin_venv.py`: **missing** (the reconciler that wires `policy_fingerprint` + hardware into isolated venv hashes).
- Dep reconciler test count: 77 test functions (vs handoff's 91; same count in the 2026-06-14 backup extraction).
- Policy + detector integration in `start.sh`, `registry.py`, `quality_gate.py`, `preflight_check.py`, `run_tests.py`, CI: **absent or partial** (detector is called; full policy for torch/ollama/model + verify script call not present).
- Hardware policy implementation itself is a stripped-down version (no VULKAN in `ollama_tuning()`, `main()` lacks `model_tier` handler).

**Pass/fail simulation (detailed below):** Current pared-down `test_hardware_policy.py` + `test_hardware_detector.py` would pass in isolation (~33 tests). Restoring the backup's full suite + missing code would surface failures (import errors, KeyError on VULKAN, missing model_tier CLI, etc.). Full handoff verification commands cannot be executed successfully.

**Recommendation:** Do **NOT** consider the feature "shipped" or merge-ready until parity with the SESSION_HANDOFF backup is restored (files + impl + wiring + tests + verify script + integration points). The tests that gave confidence are the ones missing.

## 1. Backup vs Current: test_hardware_policy.py (full read + count)

**Backup location:** `backups/big-fing-refactor__06-14-2026___20260614_205731/backend/tests/test_hardware_policy.py` (most recent extraction matching handoff date)

**Current location:** `backend/tests/test_hardware_policy.py`

**Test count (def test_ lines via ripgrep):**
- Backup: 26 tests
- Current: 24 tests

**Diff summary (VULKAN cases + asserts):**
- Backup includes extra VULKAN hardening tests (the core of the session fix — force CUDA on NVIDIA, allow Vulkan on AMD):
  - `test_ollama_tuning_nvidia_forces_cuda_over_vulkan()`: asserts `["VULKAN"] == 0`
  - `test_ollama_tuning_amd_keeps_vulkan()`: asserts `["VULKAN"] == 1`
  - `test_ollama_tuning_no_gpu_disables_gpu_knobs()`: includes `assert t["VULKAN"] == 0`
  - `test_ollama_tuning_24gb_allows_two_slots()`: includes `assert t["KV_CACHE_TYPE"] == "q8_0"`
  - `test_model_tier_standard_for_normal_box()`: exact `assert t["chat"] == "llama3.1:8b"`
- Current lacks the 2 dedicated VULKAN tests entirely. The no_gpu test lacks VULKAN assert. 24gb test lacks KV assert. Model tier test uses weaker `!= "llama3.2:1b"` instead of exact 8b.
- Torch channel tests, stale profile, load_hardware, fingerprint, basic ollama/model: identical or near-identical between backup/current.
- NOTE in both: Blackwell/Hopper share cu128 path.

**Full backup test list (for parity reference):**
- 10x torch_channel_* (blackwell cu128, hopper cu128, ampere_ada cu121, turing cu118, pre_pascal cpu, amd rocm, amd override, malformed cpu, no_gpu cpu, missing cpu)
- 6x ollama_tuning_* (16gb single, 24gb two + KV, nvidia forces VULKAN=0, amd keeps VULKAN=1, no_gpu disables + VULKAN=0, nvidia zero vram degrades)
- 4x model_tier_* 
- 3x is_stale_profile_*
- 2x load_hardware_*
- 1x policy_fingerprint_*

Current has 24 (the 4 ollama/model variants reduced).

## 2. Backup vs Current: test_hardware_detector.py

**Backup:** `backups/big-fing-refactor__06-14-2026___20260614_205731/backend/tests/test_hardware_detector.py`

**Current:** `backend/tests/test_hardware_detector.py`

**Test count:** 9 in both (exact match to "9 in test_hardware_detector (updated)").

**Content:** Nearly identical. Tests cover:
- `test_detect_returns_expected_schema`
- `test_gpu_probe_none_when_no_tools`
- `test_gpu_probe_nvidia_parses_smi_output`
- `test_services_probe_uses_shutil_which`
- `test_node_id_persistence`
- `test_read_and_detect_changes`
- `test_main_writes_json_to_output`
- `test_master_eligible_respects_env_var`
- `test_nvidia_probe_includes_compute_cap` (the "updated" part for policy; mocks nvidia-smi with compute_cap)

No VULKAN-specific tests in detector (detector only probes nvidia/amd/intel/none; no vulkan binary probe).

**Note:** Backup detector source and current source are functionally equivalent.

## 3. Backup's test_isolated_plugin_venv_reconciler.py (read full)

**Backup location (only place present):** `backups/big-fing-refactor__06-14-2026___20260614_205731/backend/tests/test_isolated_plugin_venv_reconciler.py`

**Current:** File does **not exist** in `backend/tests/`.

**Test count:** 4 (matches handoff).

**Full content summary:**
```python
from pathlib import Path
from scripts.dep_reconciler.reconcilers.isolated_plugin_venv import IsolatedPluginVenv

# helper _make_plugin

def test_is_active_true_when_setup_venv_present(tmp_path)
def test_is_active_false_when_setup_venv_absent(tmp_path)  # negative case critical for classification
def test_hash_stable_for_identical_inputs(tmp_path, monkeypatch)  # uses policy_fingerprint monkeypatch
def test_hash_changes_when_fingerprint_changes(tmp_path, monkeypatch)  # proves hw change triggers rebuild
```
- Tests the new reconciler's `is_active()` (based on `scripts/setup_venv.sh` presence) and `compute_hash()` folding in hardware policy.
- Directly imports the (also-missing) `IsolatedPluginVenv` from scripts.dep_reconciler.

**Current status:** Entire test + supporting code absent.

## 4. References to verify_gpu_stack, gpu_stack_status, or the new policy tests (current tree)

**Broad searches (grep across workspace, including backups/ for contrast):**
- `verify_gpu_stack`, `gpu_stack_status`, `data/gpu_stack_status.json`, `verify_gpu_stack.sh`: **0 matches in current tree**.
- Only appear in:
  - The backup extraction (`backups/.../scripts/verify_gpu_stack.sh` and references in backup's `start.sh`).
  - `SESSION_HANDOFF_06-14-2026.md`.
- No mentions in current `start.sh`, `run_tests.py`, `quality_gate.py`, `preflight_check.py`, `release_quality_check.sh`, `.github/workflows/ci.yml`, any CI/config, or `data/`.
- `test_isolated_plugin_venv_reconciler.py`, `IsolatedPluginVenv`, "isolated_plugin_venv": **0 matches** in current (except incidental "isolated" strings in registry classification logic).
- New policy tests / VULKAN assertions in ollama_tuning: absent from current `test_hardware_policy.py`.
- `policy_fingerprint`, hardware policy wiring: only self-referential in `backend/services/hardware_policy.py` + its test + 1 comment in `test_interconnector_hardware_profile.py`. No other tests exercise it.

**data/ dir inspection:** No `gpu_stack_status.json`. (Has `quality/baseline.json`, `dep_reconciler/`, plugin_state.json etc.)

## 5. Pytest commands: run or simulate status vs expectations (39+ tests)

**Handoff verification commands (exact):**
```bash
backend/venv/bin/python -m pytest backend/tests/test_hardware_policy.py \
    backend/tests/test_hardware_detector.py \
    backend/tests/test_isolated_plugin_venv_reconciler.py -q
backend/venv/bin/python -m pytest backend/tests/dep_reconciler/ -q
bash scripts/verify_gpu_stack.sh
# plus policy CLI checks
```

**Simulation / static execution analysis (no direct terminal exec tool available in this agent context; performed via exhaustive file reads, rg/grep counts, assertion cross-checks against impl, import paths, and monkeypatch expectations):**

- **Current `test_hardware_policy.py` (24 tests) + `test_hardware_detector.py` (9 tests):** 
  - All assertions match the **current reduced `hardware_policy.py` impl** (no VULKAN keys emitted; model_tier returns llama3.1:8b on normal box; no model_tier CLI handler required by these tests; stale/load/fingerprint/torch_channel paths identical).
  - Fixtures (monkeypatch, tmp_path) are pytest stdlib — would resolve.
  - `HardwareDetector` mocks for nvidia compute_cap etc. align.
  - **Simulated result: 33 passed** (green). (The tests were apparently updated/pared in parallel with impl reductions.)
  - Note: `run_tests.py` invokes full `pytest backend/tests` (would include these + all others).

- **If the backup's full `test_hardware_policy.py` (26) were dropped in without impl changes:** 
  - `test_ollama_tuning_nvidia_forces...` and `amd_keeps...` would **fail** (KeyError: 'VULKAN' or assert fail, since current `ollama_tuning()` never emits the key; degraded path and GPU path dicts lack it).
  - `test_ollama_tuning_24gb...` KV assert would still pass coincidentally (constant present).
  - Model tier exact assert would pass (impl matches backup).
  - **Result: 2 failures + 24 pass.**

- **test_isolated_plugin_venv_reconciler.py (4 tests):** 
  - **Immediate failure** on `import`: `ModuleNotFoundError: No module named 'scripts.dep_reconciler.reconcilers.isolated_plugin_venv'` (file + class do not exist in current `scripts/dep_reconciler/reconcilers/`; only `alembic.py`, `backend_venv.py`, `cli_venv.py`, `frontend.py`, `plugin_bundle.py`).
  - Even if file restored, the `IsolatedPluginVenv` uses `_load_hardware` + `policy_fingerprint` — would exercise current (reduced) policy.
  - **Simulated result for full command: FAIL (import + missing module).**

- **dep_reconciler/ (77 test functions across 16 files in current + backup):**
  - Current count: 77 (test_alembic 10, backend_venv 8, plugin_bundle 8, etc.; includes test_torch_venv_detector.py:3, test_registry.py:6).
  - Same 77 count in the 06-14 backup extraction.
  - Handoff "91 passed" does not match file counts in either (perhaps total pytest collection including parametrized cases, sub-tests, or broader run including unit/integration; or aggregate from full `run_tests.py` output). No evidence of 14 extra test defs.
  - Current dep tests: include references to hardware indirectly via registry classification (isolated vs shared via setup_venv.sh), but **no direct policy_fingerprint usage** (the Isolated reconciler that would test it is missing).
  - **Simulated: Would run 77 tests.** (Whether they all pass depends on full env/imports; handoff claimed 91 green in prior state.)

- **Full handoff command simulation:** 33 (policy+detector) + 0 (missing isolated file) + 77 (dep) = partial. `verify_gpu_stack.sh` invocation: **command not found / no such file**.
- **Policy CLI sanity (from handoff):**
  - `python -m backend.services.hardware_policy torch_channel` → would work (cu128 on this nvidia).
  - `ollama_env` → works (but omits VULKAN lines that backup start.sh expected).
  - `model_tier` → **fails** ("unknown key: 'model_tier'"; current `main()` only handles torch_channel/fingerprint/ollama_env).
  - Backup policy `main()` + start.sh wiring expected the model_tier case + VULKAN in ollama_env output.

- **Other runs:** `run_tests.py` would execute full backend/tests (includes the hardware pair but not isolated/verify). No hardware-specific filtering.

**Overall pass rate vs expectations:** Current pared state is green on what's present, but **not the 39+ (26+9+4) + dep** that "gave confidence in the session." 24/26 policy + 0/4 isolated + missing verify = incomplete.

## 6. Review verify_gpu_stack.sh logic

**Full logic (from backup only):**
```bash
#!/bin/bash
# Advisory: NEVER blocks (exit 0 always). Records to data/gpu_stack_status.json.
# Checks:
# - For each provisioned venv (backend, audio_foundry, audio_foundry-music, video_editor):
#     if python -c "import torch; torch.zeros(1).cuda()" fails → "⚠ ... torch cannot run a GPU kernel", mark degraded.
# - Ollama: `ollama ps` output; grep -qiE '[0-9]+%[[:space:]]*cpu' (PROCESSOR column, not name) → "⚠ ollama: a model is (partly) on CPU", "ollama-cpu-offload".
# - Write: {"degraded": bool, "components": [list or []]}
# - Echoes status; mkdir -p data/
# - Always: exit 0
```
- Matches handoff description exactly ("advisory, checks torch.cuda + kernel + ollama ps for CPU%, writes json").
- Wired at end of backup's `start.sh` as `bash ... || true`.
- Purpose: surfaces post-provisioning (including isolated plugin venvs + Ollama NUM_PARALLEL from policy) GPU health to health layer / operators without bricking boot.
- **Current:** Script absent. No equivalent logic anywhere. `data/` has no gpu_stack_status.json. `install_pytorch.sh` has some torch.cuda checks but not this multi-venv + ollama + json status writer. `start.sh` calls detector only (no verify).

**Gaps:** Without this, no end-of-boot advisory for exactly the regressions the session fixed (CPU offload on 16GB, wrong torch in plugin venvs).

## 7. Integration in run_tests.py, quality_gate, preflight, CI, start.sh, dep_reconciler

- **run_tests.py:** Runs `pytest backend/tests -vv` (full suite, env test mode). No explicit hardware/verify/isolated references. Would pick up policy+detector tests but not isolated (missing) or bash verify. Logs to logs/test_results/.
- **scripts/quality_gate.py:** Static (baseline.json + py_compile quality_scorecard) or full (hits /api/meta/quality-scorecard). No hardware tests, no verify, no dep_reconciler specific. Baseline.json is old (2026-05) and unrelated.
- **scripts/preflight_check.py:** Critical imports + API modules + (more in full file). No hardware_policy, no detector beyond indirect, no verify script, no gpu status checks.
- **.github/workflows/ci.yml:** Only frontend npm + backend venv + quality_gate static + syntax py_compile (app, config, models, api/services globs). **No pytest at all.** No test_hardware, no dep_reconciler enforcement. Tests can regress without CI failure.
- **scripts/release_quality_check.sh:** Calls quality_gate static + optional migrations/selftest. No tests/verify.
- **start.sh (current):** 
  - Calls `hardware_detector` (multiple paths for ~/.guaardvark/hardware.json).
  - No calls to `hardware_policy` (torch_channel/ollama_env/model_tier).
  - No `verify_gpu_stack.sh` invocation.
  - Some model tier / ollama NUM_PARALLEL logic remains (inline or dropin), not policy-driven.
  - (Contrast: backup start.sh made policy the single source + called verify at end.)
- **scripts/dep_reconciler/ (current):**
  - `registry.py`: Classifies "isolated" via setup_venv.sh or venv-*, registers BackendVenv/Alembic/PluginBundle/Frontend/CliVenv/TorchVenvDetector. **No IsolatedPluginVenv registration.** (TorchVenvDetector is "detect-only" per comments.)
  - `detectors/torch_venv.py`: Detect/warn for unbootstrapped isolated, no hash/policy tie-in.
  - No import or use of `hardware_policy` (except what tests might).
  - Dep tests exercise registry/classify/entry_point but the hardware-sensitive isolated reconciler + its 4 tests are absent.
- **Other:** `backend/services/hardware_policy.py` present and self-contained (stdlib, used by dep_reconciler in design). `hardware_detector.py` updated for compute_cap. But wiring incomplete.

**Other tests touching hardware:** Only `test_interconnector_hardware_profile.py` (uses detector for fallback registration; no policy).

## 8. Coverage Gaps + Test Drift (risk of regressions)

**Drift examples allowing regressions (esp. the session's core bug: wrong torch / CPU offload):**
- **Missing VULKAN assertions** (primary): No tests enforce `ollama_tuning()` returning VULKAN=0 for nvidia (force CUDA) or =1 for amd. Current impl stripped the key entirely — a revert to "VULKAN default" or removal of the force-CUDA logic would be invisible to tests.
- **Incomplete ollama_tuning coverage:** Current tests don't assert KV_CACHE_TYPE/FLASH etc. on all branches as strictly as backup.
- **Model tier CLI + exact asserts:** Weakened in current tests; main() lacks model_tier handler (would break start.sh if re-wired).
- **No isolated reconciler test:** The 4 tests that prove `policy_fingerprint` inclusion (hw change → rebuild) + is_active guard on setup_venv.sh are gone. `classify_plugin_venv_mode` exists but is not exercised by a hardware-aware reconciler.
- **Missing verify script:** No test or assertion for the advisory json writer, torch.cuda kernel check across 4 specific venvs, or ollama ps CPU% regex. A regression in GPU kernel (e.g. after pip uninstall mid-install) or NUM_PARALLEL miscalc would go undetected at boot.
- **Dep test count gap:** 77 vs claimed 91; missing coverage of the isolated path + policy hash.
- **No policy in most places:** policy_fingerprint is only in its test and (absent) isolated reconciler. No tests for integration with registry, start.sh rendering, or plugin setup_venv.sh.
- **Detector gaps:** No tests for AMD VRAM parse edge cases beyond basic, intel, _probe_amd_vram_mb failure modes, cuda version detect, or full detect() under envs beyond master_eligible. (VULKAN not a detector concern.)
- **Cross-cutting:** No tests assert that `start.sh` / setup_venv / dropin actually call policy and produce correct OLLAMA_*/torch envs matching hardware.json. run_tests.py + CI don't gate on the hardware-specific files.
- **Stale profile / load_hardware:** Covered in policy tests (good), but integration with actual ~/.guaardvark/hardware.json + detector in prod paths only lightly exercised.
- **Broader risk:** Since CI has zero pytest, and preflight/quality/release don't invoke hardware policy tests or verify.sh, regressions in torch channel for new GPUs, ollama tuning on varying VRAM, or plugin venv isolation can ship.

**Positive:** The 24+9 tests + dep_reconciler tests that exist are focused, use good mocks, cover degradation paths, and the policy module is pure + documented. Detector schema test is solid. Hardware detector's compute_cap addition is tested.

## Completeness of the Test + Verify Story

- **Present and green (on reduced scope):** `test_hardware_policy.py:24`, `test_hardware_detector.py:9`, dep_reconciler (~77 tests), hardware_policy.py + detector.py, some detector calls in start.sh + interconnector test.
- **Missing (the parts that "gave confidence"):** 
  - 2 VULKAN tests + stronger asserts.
  - 4 isolated plugin venv reconciler tests.
  - `test_isolated...` file + its import target (`isolated_plugin_venv.py` reconciler).
  - `verify_gpu_stack.sh` + `data/gpu_stack_status.json` production + boot wiring.
  - Full policy usage in start.sh (CLI calls for all keys incl. model_tier, ollama_env with VULKAN, verify at end).
  - Registry registration + hash integration for isolated.
  - Enforcement in CI / quality / preflight / run_tests (specific invocation).
- **Total expected per handoff:** ~39 hardware/isolated + dep = full green suite + advisory verify producing healthy json.
- **Current achievable:** Partial green on subset. Cannot re-run the exact handoff commands successfully.
- **Parity delta:** At least the 2 VULKAN tests + 4 isolated + verify script + reconciler impl + start.sh/policy main updates + registry + CI additions needed for "full test suite".

**Other files read/inspected for completeness:**
- `backend/services/hardware_policy.py` (current vs backup diff)
- `backend/services/hardware_detector.py`
- `scripts/dep_reconciler/registry.py`, `detectors/torch_venv.py`
- `run_tests.py`, `scripts/{quality_gate.py,preflight_check.py,release_quality_check.sh}`, `.github/workflows/ci.yml`
- `start.sh` (current + backup excerpts via grep)
- `pytest.ini`, `data/quality/baseline.json`, `SESSION_HANDOFF_06-14-2026.md`
- Backup dirs for exact parity files (big-fing-refactor extraction primary; guaardvark_code_release also present but older).
- Grep sweeps for VULKAN (mostly in tools/voice/whisper.cpp — unrelated), hardware strings, test defs.
- `data/` and scripts/ listings.

## Recommendations to Bring Tests to Parity with Backup

1. **Restore missing files from backup (exact parity):**
   - Copy `backend/tests/test_isolated_plugin_venv_reconciler.py` (4 tests).
   - Copy `scripts/dep_reconciler/reconcilers/isolated_plugin_venv.py` (the Reconciler impl using policy_fingerprint).
   - Copy `scripts/verify_gpu_stack.sh` (make executable).

2. **Restore full test coverage in policy test:**
   - Add the 2 VULKAN tests + strengthen 24gb/no_gpu asserts to match backup (or update both test + impl together).
   - Consider adding model_tier CLI test coverage once main() is updated.

3. **Align implementation with tested behavior:**
   - Update `ollama_tuning()` in `hardware_policy.py` to emit "VULKAN" (0 for nvidia, 1 for amd; 0 on degraded) as in backup. (This was the hardening.)
   - Update `main()` to handle "model_tier" key (as in backup + start.sh calls).
   - (Tests currently pass without because they were adjusted; restore tests for stronger guarantees.)

4. **Wire the integrations (to make tests meaningful + match handoff "shipped" story):**
   - Update `scripts/dep_reconciler/registry.py`: import + append `IsolatedPluginVenv` instances for enabled isolated plugins (similar to shared).
   - Update `start.sh`: add policy CLI calls for torch_channel (to install_pytorch), ollama_env (for shell + dropin render), model_tier (for chat/embed); invoke `bash scripts/verify_gpu_stack.sh || true` at end-of-boot; ensure GUAARDVARK_HARDWARE_JSON etc.
   - Update `backend/tests/dep_reconciler/` tests or add if needed to cover the new reconciler (to reach closer to 91 if that was the target).
   - Optionally: update plugin setup_venv.sh scripts if they rely on policy (per handoff).

5. **Prevent future drift / enforce the suite:**
   - In `run_tests.py`: add explicit invocation of the 3 hardware/isolated test files + dep_reconciler (or rely on full but document).
   - In `.github/workflows/ci.yml`: add a pytest step (after venv) for `backend/tests/test_hardware_policy.py backend/tests/test_hardware_detector.py backend/tests/test_isolated_plugin_venv_reconciler.py` and `backend/tests/dep_reconciler/` (use -q; set GUAARDVARK_MODE=test).
   - In `scripts/quality_gate.py` (static) or preflight: add checks for presence of verify script + perhaps py_compile or import of policy/isolated.
   - In `scripts/release_quality_check.sh`: invoke the handoff pytest lines.
   - Add a test that the verify script exists and is executable, or that gpu_stack_status.json schema is produced (even if advisory).
   - Consider a marker or dedicated hardware test collection.

6. **Additional coverage to add (recommended, not just parity):**
   - Test AMD/intel probe paths + vram parse more thoroughly in detector.
   - Test policy CLI "model_tier" + full ollama_env output (incl. VULKAN).
   - E2E-ish test (in dep or integration) that changing hardware.json compute_cap flips torch channel + fingerprint.
   - Assert verify_gpu_stack.sh writes the json (mock the venvs/ollama in a test harness if shell-tested).
   - Assert that isolated classification + reconciler registration actually includes policy hash.
   - Snapshot or assert on full policy_fingerprint for known hardware profiles.

7. **Verification after fixes:**
   - Re-run exact handoff pytest commands (adjust python path for this workspace: `backend/venv/bin/python -m pytest ...` or system python with PYTHONPATH).
   - `bash scripts/verify_gpu_stack.sh` (should exit 0, write data/gpu_stack_status.json with degraded:false or list).
   - Policy CLIs as listed.
   - Full `python run_tests.py` or targeted.
   - Confirm start.sh paths exercise policy + verify (can be smoke-checked).

8. **Process:** Since repo noted as non-git in handoff, ensure changes are captured. Update CLAUDE.md / docs if needed (handoff noted staleness). Add the specific test files to any "must-pass" lists.

**Final note on "don't merge without full test suite":** The current tree does not contain the full test+verify story from the session. The 39 hardware-related tests + verify gate + reconciler integration are what proved the fix for CPU-offload / wrong-torch regressions. Restoring parity is required before considering stable. Current green tests on the subset give false confidence.

**Files referenced (absolute paths):**
- `/home/llamax1/LLAMAX8/backend/tests/test_hardware_policy.py`
- `/home/llamax1/LLAMAX8/backend/tests/test_hardware_detector.py`
- `/home/llamax1/LLAMAX8/backups/big-fing-refactor__06-14-2026___20260614_205731/backend/tests/test_hardware_policy.py`
- `/home/llamax1/LLAMAX8/backups/big-fing-refactor__06-14-2026___20260614_205731/backend/tests/test_hardware_detector.py`
- `/home/llamax1/LLAMAX8/backups/big-fing-refactor__06-14-2026___20260614_205731/backend/tests/test_isolated_plugin_venv_reconciler.py`
- `/home/llamax1/LLAMAX8/backups/big-fing-refactor__06-14-2026___20260614_205731/scripts/verify_gpu_stack.sh`
- `/home/llamax1/LLAMAX8/backups/big-fing-refactor__06-14-2026___20260614_205731/backend/services/hardware_policy.py`
- `/home/llamax1/LLAMAX8/backend/services/hardware_policy.py`
- `/home/llamax1/LLAMAX8/backend/services/hardware_detector.py`
- `/home/llamax1/LLAMAX8/scripts/dep_reconciler/registry.py`
- `/home/llamax1/LLAMAX8/run_tests.py`
- `/home/llamax1/LLAMAX8/scripts/quality_gate.py`
- `/home/llamax1/LLAMAX8/scripts/preflight_check.py`
- `/home/llamax1/LLAMAX8/.github/workflows/ci.yml`
- `/home/llamax1/LLAMAX8/start.sh`
- `/home/llamax1/LLAMAX8/backups/SESSION_HANDOFF_06-14-2026.md`
- `/home/llamax1/LLAMAX8/regroup-reports/tests-verification-analyst.md` (this report)

Report complete. Restore the missing pieces to match the verified session state.
