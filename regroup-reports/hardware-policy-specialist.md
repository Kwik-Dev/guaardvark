# Hardware Policy & Detector Specialist Report

**Date:** 2026-06-14  
**Expert Role:** Hardware Policy & Detector Specialist  
**Scope:** Analysis of "big-fing-refactor" backup snapshot (canonical per SESSION_HANDOFF/handoff context + regroup reports) vs current main branch working tree at /home/llamax1/LLAMAX8. Focus: single source-of-truth `backend/services/hardware_policy.py` (stdlib, pure) + `hardware_detector.py`, integration in start.sh/install_pytorch.sh/plugin scripts, tests, policy_fingerprint, _is_stale_profile, related files (gpu_resource_policy.py, hardware_service.py), AMD/legacy completeness, regressions/risks/reconciliation.  
**Key Deliverable:** Full analysis + recommendations. File saved at: `/home/llamax1/LLAMAX8/regroup-reports/hardware-policy-specialist.md`

## Executive Summary

The core "big-fing-refactor" feature (one pure-stdlib `hardware_policy.py` as source of truth for `torch_channel` (via detector `compute_cap` for Blackwell=cu128 etc.), `ollama_tuning` (NUM_PARALLEL derived from VRAM to fix the 4-parallel CPU-offload bug on 16GB), `model_tier`, `policy_fingerprint` for rebuild triggers) is **partially landed in current tree but incomplete and drifted** vs the canonical backup snapshot described in handoff + regroup reports (dep-reconciler-expert.md, provisioning-reviewer.md).

- **Current strengths (improvements vs older state):** `hardware_detector.py` enhanced with `compute_cap` probe via nvidia-smi (critical for >=sm_90+ torch_channel); `_load_hardware` + `_is_stale_profile` guard for missing `compute_cap` (prevents silent CPU fallback on good NVIDIA); `policy.py` implements `torch_channel`/`ollama_tuning`/`model_tier`/`policy_fingerprint` + CLI (torch_channel | ollama_env | fingerprint); tests cover compute_cap paths + stale guard; audio_foundry `setup_venv.sh` correctly calls `python -m backend.services.hardware_policy torch_channel` and passes `GUAARDVARK_TORCH_CHANNEL` to `install_pytorch.sh`; install_pytorch.sh honors the env var (single-source comment at lines ~227-233); detector _probe_gpu_nvidia includes compute_cap query.
- **Current regressions/drift vs backup snapshot (key observed facts confirmed via reads + reports):** 
  - `backend/services/hardware_policy.py` (current): **missing "VULKAN" key+logic** in `ollama_tuning` (backup forces `0` for nvidia to prevent silent Vulkan fallback; see also ollama-systemd-dropin.conf comment). main() CLI lacks "model_tier" command (backup has it; start.sh calls it per handoff). Docstring incomplete (current lists only torch_channel | ollama_env | fingerprint).
  - **Tests differ:** current `test_hardware_policy.py` has relaxed `model_tier` assert (`assert t["chat"] != "llama3.2:1b"`) and **fewer cases** (no VULKAN-specific tests; backup had extra). `test_hardware_detector.py` covers compute_cap but current is post-enhancement.
  - **start.sh / integration:** Calls `hardware_detector` (multiple times, early + ensure_hardware_profile) but **no calls to `hardware_policy` CLI for model_tier/ollama_env/fingerprint**. Model bootstrap (lines ~1359-1389) uses direct python json parse + inline RAM/arch logic (duplicate of `model_tier`). Ollama tuning (lines ~1239-1252) hardcodes `OLLAMA_NUM_PARALLEL=2` (with nvidia-smi guard for flash), **not derived from policy `ollama_tuning` (which uses VRAM for 1/2 slots)**. Dropin install is static copy (not rendered from policy). Comments note discrepancy (e.g. "start.sh shell path defaults to 2").
  - `install_pytorch.sh`: Honors `GUAARDVARK_TORCH_CHANNEL` (good remnant), but start.sh/backend paths call bare script (no env from policy).
  - `policy_fingerprint` + `_is_stale_profile`: Implemented + unit-tested in policy (correct: fingerprints decisions not raw hw; stale only for nvidia missing compute_cap). **Not used outside** (only internal to `_load_hardware` + tests + main "fingerprint" key). Reports confirm: "compute_hash / manifests in no current reconciler calls `hardware_policy.policy_fingerprint(...)`. The folding is absent." Intended for dep_reconciler rebuild triggers (per handoff).
  - **Other:** No `GUAARDVARK_TORCH_CHANNEL` export from detector/policy in main start.sh paths (only in audio plugin script). Model bootstrap ignores policy entirely.
- **Risks/Implications:** Without VULKAN=0 enforced via policy on nvidia, risk of silent bad backend (Vulkan fallback despite CUDA-capable sm_120 card; dropin hardcodes it today but policy is source of truth). Without model_tier CLI + start.sh wiring, model selection may fallback to wrong tier or stale inline logic (re-introduces per-box bootstrap bugs). Fingerprint absence means no hw-drift (e.g. GPU swap, box restore, different compute_cap) rebuild triggers for torch venvs or ollama tuning. 16GB NUM_PARALLEL bug (4->1 fix via VRAM) not live in start.sh paths (hardcoded). Feature "one hardware policy -> every env" not achieved; remnants exist (policy module, detector compute_cap, audio script, install_pytorch honor) but wiring incomplete (regression from backup canonical).
- **Other files in "backup":** `gpu_resource_policy.py` (current at backend/services/): **Unrelated** to this feature. It is a composition layer for GPU reclaim (free_comfyui_vram, evict_ollama_models, gpu_session hooks for orchestrator). Different concern (exclusivity + VRAM budget) from hardware_policy's provisioning decisions. `hardware_service.py` (current): **Legacy/unrelated**. Old class using torch/psutil for training recs (batch_size etc based on vram); predates or parallels the stdlib pure policy/detector. Not using or referenced by hardware_policy. No evidence of "gpu_resource_policy.py or hardware_service.py" as part of the big-fing-refactor handoff (they pre-exist or are orthogonal).
- **AMD/legacy completeness (per handoff open items):** Partial. 
  - **torch_channel:** Full (amd -> rocm_whl (overridable), nvidia via _compute_major on compute_cap (>=9=cu128/Blackwell/Hopper, >=8=cu121, >=6=cu118), else cpu/legacy). Handles malformed/missing as cpu (safe).
  - **ollama_tuning:** Supports nvidia/amd (vram-based: >=20000MB -> 2 parallel/loaded; else 1); degrades gracefully for none/0-vram (NUM=1, FLASH=0, MAX=1). No AMD-specific beyond vram (correct, since AMD uses ROCm not CUDA/Vulkan). 
  - **model_tier:** Supports arm (aarch64/arm64 -> 1b) + low ram (<=8GB ->1b); else 8b. gpu param reserved for future.
  - **Detector:** AMD probe best-effort via rocm-smi (vram only; "Unverified on real AMD hardware" per docstring line ~166-168); falls to none if no rocm-smi. Legacy (no tools) -> {"vendor":"none"}. Intel limited. nvidia compute_cap always probed.
  - **Gaps vs complete:** Detector AMD vram unverified; start.sh ollama tuning still nvidia-smi-centric (not policy-driven, misses AMD); model bootstrap inline (not policy.model_tier); no policy_fingerprint in rebuilds affects AMD/GPU-swap cases too. VULKAN logic nvidia-only (correct). Feature mostly complete in policy/detector logic; integration/start.sh/AMD verification are open items.
- **Overall status:** Feature heart (policy as stdlib source) present and correct in isolation (with compute_cap enhancement + stale guard), but **not "live"** across envs (start.sh, reconciler, dropin, model bootstrap). Current is a regression from backup snapshot's fuller wiring. High risk of re-introducing original bugs (wrong torch channel, 4-parallel on 16GB, silent Vulkan, stale hw cache, inconsistent model tiers). Reconciliation needed.

**Recommendation priority:** HIGH (core of "every env" fix). Reconcile by porting backup wiring (CLI model_tier, start.sh calls for ollama_env + model_tier + TORCH_CHANNEL export + rendered dropin from policy, fingerprint in reconciler compute_hash, VULKAN in ollama_tuning + tests, full AMD verification). See detailed steps below.

## 1. Full Reads Performed (Task 1)

### 1.1 Current Versions (absolute paths in /home/llamax1/LLAMAX8/)
- `backend/services/hardware_policy.py` (180 lines; full read): Pure stdlib. Key fns:
  - `_compute_major` (16-23): Parses gpu["compute_cap"] major.
  - `torch_channel(gpu, rocm_whl=DEFAULT_ROCM_WHL)` (26-47): nvidia major>=9→cu128, >=8→cu121, >=6→cu118, amd→rocm, else cpu. Uses compute_cap from detector.
  - `ollama_tuning(gpu)` (50-84): VRAM-derived NUM_PARALLEL (1 or 2), MAX_LOADED, FLASH=1 only for nvidia/amd + vram>0; else degraded (FLASH=0 etc). **No "VULKAN" key**.
  - `model_tier(ram_gb, gpu, arch)` (87-100): arm/low-ram→llama3.2:1b else llama3.1:8b + nomic.
  - `policy_fingerprint(hardware)` (103-115): sha256[:16] of "torch=...|ollama_np=...|tier=..." decisions.
  - `_is_stale_profile(profile)` (118-132): True for nvidia without compute_cap (docstring notes added 2026-06-14).
  - `_load_hardware()` (135-150): Reads cached ~/.guaardvark/hardware.json (via detector.read_profile) **if not stale**, else live detect(). Uses env GUAARDVARK_HARDWARE_JSON.
  - `main(argv)` (153-174): CLI keys: torch_channel | ollama_env (prints OLLAMA_*=) | fingerprint. **No model_tier**.
- `backend/services/hardware_detector.py` (240 lines; full read): Focus _probe_gpu_nvidia (136-158):
  - Queries nvidia-smi with `name,memory.total,driver_version,compute_cap` (explicitly added for policy).
  - Returns {"vendor":"nvidia", "model", "vram_mb", "driver", "compute_cap": parts[3], "cuda":...}.
  - AMD: rocm-smi for vram only (best-effort, unverified docstring).
  - Intel: minimal.
  - _probe_gpu tries nvidia then amd then intel.
  - read_profile, detect_changes, main --output for start.sh.
- `backend/tests/test_hardware_policy.py` (150 lines; full read): 18 tests. Covers torch_channel (blackwell/hopper/ampere/turing/pre-pascal/amd/malformed/missing), ollama (16gb/24gb/no-gpu/zero-vram), model_tier (small-ram/arm/high-ram/standard; relaxed assert), _is_stale_profile (3 cases), _load_hardware (2 monkeypatch cases), policy_fingerprint (stable/sensitive). **No VULKAN tests**.
- `backend/tests/test_hardware_detector.py` (111 lines; full read): Schema, gpu none, nvidia parse (smi mock without asserting compute_cap in basic), services, node_id, changes, main, master_eligible, **nvidia_probe_includes_compute_cap** (89-110; fake_run asserts "compute_cap" query + "12.0" parse).
- `start.sh` (relevant sections full read; ~1650 lines total): 
  - Early detector call (1046): `python3 -m backend.services.hardware_detector --output ...`
  - Model bootstrap (~1359-1389): Parses hardware.json directly for ram/arch; inline if <=8 or arm → llama3.2:1b else 8b (dupe of model_tier). No policy call.
  - Ollama tuning (~1239-1252): Hardcode NUM_PARALLEL=2, KV=q8_0 (if nvidia-smi), FLASH=1 (if nvidia); KEEP=15m. Guarded by GUAARDVARK_OLLAMA_TUNING.
  - Dropin install (~1265-1278): Copies static scripts/ollama-systemd-dropin.conf (if nvidia-smi + sudo).
  - Later detector (~1526,1534): ensure_hardware_profile (venv or system python).
  - No `hardware_policy` invocations. (Grep confirmed only detector.)
- `scripts/install_pytorch.sh` (relevant ~86-350+ full read): 
  - Respects GUAARDVARK_HARDWARE_JSON; _hardware_json_says_amd.
  - NVIDIA path: nvidia-smi for compute_cap directly; **if [ -n "${GUAARDVARK_TORCH_CHANNEL:-}" ]** then honor (from policy), else table (major>=12/9=cu128 etc, with comments).
  - Calls with TARGET_VENV=... GUAARDVARK_TORCH_CHANNEL=... in some paths.
  - AMD/CPU/MPS branches.
- `plugins/audio_foundry/scripts/setup_venv.sh` (full read): Calls `TORCH_CHANNEL="$("$BACKEND_PY" -m backend.services.hardware_policy torch_channel ...)"`; passes to install_pytorch --venv. Good use of policy (for its 2 venvs + verify).
- `scripts/ollama-systemd-dropin.conf` (full read): Static with NUM_PARALLEL=1, VULKAN=0, comments (2026-06-14 changes for 4->1 fix, "reconcile if you change this" with start.sh default=2; "leaving Vulkan enabled only adds a silent fallback path").
- `backend/services/gpu_resource_policy.py` (full top + structure read): Unrelated (GPU reclaim composition: free_comfyui, evict_ollama, orchestrator hooks). Design doc reference, no overlap with hardware_policy.
- `backend/services/hardware_service.py` (full top + structure read): Legacy (torch/psutil training recs: batch/4bit/lora based on vram buckets). No policy/detector use.
- Other: `backend/api/node_api.py` (reads hardware.json + own live nvidia probes, no policy); `backend/cuda_config.py` (runtime torch caps, no policy); reports (dep-reconciler-expert.md, provisioning-reviewer.md) describing backup canonical vs current drift.

### 1.2 Backup Versions (inferred from key observed facts in prompt + regroup reports describing "canonical" handoff snapshot 2026-06-14; no separate on-disk copies found via exhaustive grep/list_dir on archives/DEV3/Downloads/Desktop/.git for dated "big-fing-refactor" or VULKAN-in-ollama_tuning py; current tree is post-drift main)
- **hardware_policy.py (backup canonical per facts + reports):** Includes "VULKAN" key in ollama_tuning() (forces 0 on nvidia); main() has "model_tier" command (and start.sh calls `python -m ... model_tier`); fuller docstring; likely ollama_tuning returns VULKAN + MAX_LOADED_MODELS etc. (dropin comments reference policy rendering).
- **hardware_detector.py (backup):** Enhanced with compute_cap (same as current; "was enhanced in backup/current"); _load_hardware stale guard (same).
- **test_hardware_policy.py (backup):** Extra VULKAN-specific tests; stricter model_tier asserts (not the relaxed `!= "llama3.2:1b"`).
- **test_hardware_detector.py (backup):** Similar, perhaps fewer post-enhance cases.
- **start.sh (backup):** Calls policy for model_tier (and ollama_env for dropin rendering); uses policy-derived NUM_PARALLEL (from vram via ollama_tuning, not hardcoded 2); exports TORCH_CHANNEL from policy; renders dropin from policy.ollama_tuning(); model bootstrap via policy.
- **install_pytorch.sh (backup):** Same override logic (comments reference policy as single source).
- **Reports confirm drift:** "Current vs Backup Drift... Model selection: No policy; inline only. (Also: policy.py CLI lacks `model_tier` handler...)"; "Ollama (NUM_PARALLEL + drop-in): Missing. Hardcoded 2..."; "video_editor setup_venv.sh absent in current"; "fingerprint folding absent"; "backup ollama-systemd-dropin.conf updated header" referencing policy render + MAX_LOADED.
- No full text of backup .py files on disk (only current + descriptive "inferred spec" in reports). Analysis uses current reads + explicit "key observed facts" + report excerpts for diffs.

(No terminal `diff` possible without exec tool or on-disk backup files; comparison via line-by-line reads of current + cross-referenced facts/reports.)

## 2. Detailed Comparison (Key Diffs, Line Refs; Task 2-3)

### hardware_policy.py
- **Current lines 50-84 (ollama_tuning):** No VULKAN. Returns NUM_PARALLEL, KV_CACHE_TYPE, FLASH_ATTENTION, MAX_LOADED_MODELS, KEEP_ALIVE. VRAM logic at 72-77 ( >=20000 →2 else 1).
- **Backup (per facts):** Adds "VULKAN": 0 for nvidia (to prevent silent fallback; see dropin comment line 33-34).
- **Current lines 153-174 (main):** Keys torch_channel (165), fingerprint (167), ollama_env (169). Docstring 154-157 incomplete.
- **Backup:** Has model_tier case; start.sh calls it. (Current fingerprint test uses it internally.)
- **Current lines 103-115 (policy_fingerprint):** Correct impl (decisions only; uses torch/ollama_np/tier).
- **Current lines 118-132 (_is_stale_profile):** Correct (nvidia-only for compute_cap; extensible comment).
- **Current lines 135-150 (_load_hardware):** Uses stale guard + detector (good).
- **Diffs:** Current lacks VULKAN + model_tier CLI (regressions). Fingerprint/_stale correct but unused broadly.
- **AMD/legacy in policy:** Covered (see exec summary).

### hardware_detector.py
- **Current lines 136-158 (_probe_gpu_nvidia):** Full compute_cap query + parse. Matches enhancement.
- **Current _probe_gpu_amd (160-187):** vram via rocm-smi (unverified per 166-168); no compute_cap (not needed).
- **_load_hardware guard (policy 148):** Rejects stale nvidia w/o compute_cap → live detect (addresses "silent-CPU failure").
- **Backup:** Same (enhanced in both "backup/current").
- **Tests:** Current detector test 89-110 explicitly mocks the compute_cap query + "12.0".

### Tests
- **Current test_hardware_policy.py:** 18 tests; relaxed model_tier (100); no VULKAN. fingerprint test 143-149 good (sensitive to cap change).
- **Backup:** "extra VULKAN-specific tests; current has relaxed model_tier assert and fewer cases."
- **Current test_hardware_detector.py:** Good nvidia cap coverage.

### start.sh / install_pytorch.sh / integration
- **Current start.sh:** Detector only (1046,1526,1534). Model inline ~1381-1389 (dupe logic). Ollama hardcode ~1240 (NUM=2 default). Dropin static copy.
- **Backup (per facts/reports):** start.sh calls policy model_tier + ollama_env (for rendering dropin + NUM from vram); full wiring.
- **Current install_pytorch.sh ~229-233,239:** Honors GUAARDVARK_TORCH_CHANNEL (comment: "single source of truth"); else fallback table (dupe of policy torch_channel logic at 240-276).
- **Current audio setup_venv.sh:17:** Does call policy (good, but not main paths).
- **Reports:** "start.sh RENDERS this drop-in from backend.services.hardware_policy.ollama_tuning()" (backup only); "Current vs. Backup Drift" lists 5+ re-intro bugs.

### policy_fingerprint / _is_stale_profile (Task 4)
- Defined/used only in policy.py (103,118,148,167) + tests (104-118,121-140,143).
- Correct: fingerprint folds decisions (torch+np+tier) for rebuilds; stale guards exactly the "missing compute_cap" case added 2026-06-14.
- **Not wired:** No calls in dep_reconciler (reports: "The folding is absent."), start.sh, cuda_config, node_api, etc. "Intended: policy_fingerprint() folded into isolated reconciler compute_hash()." (provisioning report 201).
- **Risk:** hw changes (GPU restore, cap diff) won't trigger rebuilds (as designed to prevent "wrong torch" disease).

### Implications / Risks (Task 5)
- **Silent bad backend:** nvidia may get Vulkan (if not dropin) despite policy intent.
- **4-parallel CPU-offload bug:** Not fixed live (start.sh hardcodes 2; policy would give 1 on 16GB per 16k vram).
- **Model selection fallback:** Inline dupe in start.sh may diverge from policy.model_tier (e.g. future VRAM-aware changes).
- **No rebuild on hw change:** Fingerprint unused → stale torch in venvs on GPU swap/restore.
- **Stale cache:** Guard exists but only protects _load_hardware (internal); detector calls in start.sh write fresh but policy not always consulted.
- **AMD/legacy:** Risk of unverified vram on AMD (detector) leading to wrong ollama NUM; start.sh nvidia-smi bias.
- **"One policy -> every env" broken:** audio plugin uses it; main backend/ollama/models/plugins do not.
- Reports confirm: "The 'one hardware policy → every environment... is NOT achieved in the current tree."

### Other Files (Task 6)
- `gpu_resource_policy.py`: Unrelated (see exec + read summary). VRAM reclaim, not provisioning policy.
- `hardware_service.py`: Unrelated legacy (torch-based training recs; no overlap with stdlib policy/detector).
- No other "backup" files for this feature (reports confirm isolated reconciler etc. also missing in current).

### AMD/Legacy Completeness (handoff open items)
See exec summary. Logic in policy/detector is solid for AMD (rocm torch, vram ollama) + legacy (cpu degrade, arm tier). Gaps are verification (AMD unverified), integration (start.sh not policy-driven, misses AMD), and full fingerprint use. VULKAN nvidia-only (correct).

## 3. Recommended Reconciliation Steps (Task 7)

1. **Restore to backup canonical in hardware_policy.py:**
   - Add VULKAN:0 to ollama_tuning() for nvidia (return in dict; update ollama_env CLI path).
   - Add model_tier case to main() (e.g. `elif key == "model_tier": ... print json or chat/embed`); update docstring.
   - Add/update tests for VULKAN + strict model_tier.

2. **Wire start.sh (and related):**
   - Export TORCH_CHANNEL from `python -m backend.services.hardware_policy torch_channel` early.
   - Use for install_pytorch + GUAARDVARK_TORCH_CHANNEL.
   - Call for model bootstrap: `python -m ... model_tier` (parse ram/arch/gpu from hw or pass).
   - Derive ollama exports from `python -m ... ollama_env` (or call ollama_tuning); render dropin dynamically from policy (or at least use its values; reconcile NUM_PARALLEL default).
   - Update dropin header per backup.

3. **Wire dep_reconciler (per reports):**
   - Implement missing isolated_plugin_venv.py (use lazy policy_fingerprint in compute_hash + manifests including setup_venv.sh).
   - Register in registry.py build_active_reconcilers.
   - Add policy_fingerprint to backend_venv etc. hashes.
   - Add the 4 tests.

4. **Other:**
   - Update ollama dropin + start.sh comments to match (VULKAN via policy).
   - Add end-of-boot verify (per reports).
   - Enhance AMD: verify rocm-smi in detector tests; make start.sh ollama tuning policy-driven (not nvidia-smi only).
   - Ensure _load_hardware used where detector profiles are read (for stale guard).
   - Video_editor: add setup_venv.sh mirroring audio (per backup).
   - Test: run start.sh paths + policy CLI + fingerprint drift scenarios.

5. **Verification:** After changes, `python -m backend.services.hardware_policy torch_channel` etc.; boot with/without hw.json; swap GPU sim; AMD box test; grep for remaining inline dupe logic.

This reconciles the heart of the refactor: policy as single source, live everywhere, with fingerprint for drift safety. Current partial state re-risks the original bugs the feature was built to fix.

**References (absolute paths + lines):**
- Current policy: /home/llamax1/LLAMAX8/backend/services/hardware_policy.py:50 (ollama no VULKAN), :153 (main CLI), :103 (fingerprint), :118 (_is_stale), :135 (_load).
- Detector: .../hardware_detector.py:139 (nvidia query with compute_cap).
- start.sh: /home/llamax1/LLAMAX8/start.sh:1239 (hardcode ollama), :1364 (inline model json), :1046 (detector only).
- install: .../install_pytorch.sh:229 (honor GUAARDVARK_TORCH_CHANNEL).
- Tests: .../test_hardware_policy.py:100 (relaxed), no VULKAN.
- Reports: /home/llamax1/LLAMAX8/regroup-reports/provisioning-reviewer.md:164 (drift list), :201 (fingerprint absent); dep-reconciler-expert.md:15 (absent folding), :38 (intended hash).
- Dropin: /home/llamax1/LLAMAX8/scripts/ollama-systemd-dropin.conf:33 (VULKAN comment), :36 (static NUM=1).
- Audio use: .../plugins/audio_foundry/scripts/setup_venv.sh:17 (policy call).
- Unrelated: .../gpu_resource_policy.py:1 (design), .../hardware_service.py:7 (legacy class).

**Status:** Analysis complete. Feature core present but integration incomplete → high regression risk vs backup. Reconcile per steps.