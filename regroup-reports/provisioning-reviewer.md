# Provisioning Scripts & Boot Integration Reviewer Report

**Date:** 2026-06-14  
**Workspace:** /home/llamax1/LLAMAX8  
**Focus:** Whether "applied + verified by ./start.sh" to *every* environment (backend + isolated plugin venvs + Ollama) is actually achieved in the *current* tree. Root context from SESSION_HANDOFF (backups/SESSION_HANDOFF_06-14-2026.md) and backup snapshot `backups/big-fing-refactor__06-14-2026___20260614_205731/`.

All observations derived from direct `read_file`, `list_dir`, and `grep` across current tree vs. the unpacked "backup" snapshot (the state containing the intended fix per handoff + diffs).

---

## 1. Files Read (Full + Key Sections)

### scripts/verify_gpu_stack.sh
- **Current tree:** **ENTIRELY MISSING**. No file at `scripts/verify_gpu_stack.sh`. Grep for the name across workspace yields zero hits in current sources (only references live in backups/ dir and SESSION_HANDOFF).
- **Backup:** Full file present at `backups/big-fing-refactor__06-14-2026___20260614_205731/scripts/verify_gpu_stack.sh` (51 lines).

```bash
#!/bin/bash
# scripts/verify_gpu_stack.sh
# Advisory verification: does each provisioned venv run a GPU kernel, and is
# Ollama serving on the GPU? NEVER blocks boot — exits 0, records degraded
# state to data/gpu_stack_status.json for the health layer.
...
check_venv "backend"             "$REPO_ROOT/backend/venv/bin/python"
check_venv "audio_foundry"       "$REPO_ROOT/plugins/audio_foundry/venv/bin/python"
check_venv "audio_foundry-music" "$REPO_ROOT/plugins/audio_foundry/venv-music/bin/python"
check_venv "video_editor"        "$REPO_ROOT/plugins/video_editor/venv/bin/python"
...
# Writes {"degraded": bool, "components": [...] } to data/gpu_stack_status.json
# Always `exit 0`.
```

### start.sh (Key Sections per task: ~840+, 1240+, 1410+, 2160+ + full context)
- **Current:** `/home/llamax1/LLAMAX8/start.sh` (~2157 lines).
- **Backup:** `backups/.../start.sh` (longer, ~2190+ lines).

**Critical diffs (policy integration, torch, Ollama, model, verify):**

- **Backend torch install (around 848 in both):**
  - Current (lines 848-849):
    ```bash
    if [ -f "$SCRIPT_DIR/scripts/install_pytorch.sh" ]; then
        bash "$SCRIPT_DIR/scripts/install_pytorch.sh" >> "$SETUP_LOG" 2>&1 || vader_warn ...
    ```
    (No `GUAARDVARK_TORCH_CHANNEL`, no policy call. Later dep_reconciler call is `--only backend_venv,cli_venv`.)
  - Backup (lines 851-853):
    ```bash
    GUAARDVARK_TORCH_CHANNEL="$("$VENV_DIR/bin/python" -m backend.services.hardware_policy torch_channel 2>/dev/null || true)" \
        bash "$SCRIPT_DIR/scripts/install_pytorch.sh" ...
    ```
    (Explicit comment: "Make hardware_policy the authority for the backend venv's torch channel too (same source the plugin setup_venv.sh scripts use)").

- **Ollama NUM_PARALLEL / tuning (around 1240):**
  - Current (lines 1239-1249, 1645):
    ```bash
    if [ "${GUAARDVARK_OLLAMA_TUNING:-1}" != "0" ]; then
        export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-2}"   # <--- hardcoded default 2
        ...
    ```
    + later unconditional:
    ```bash
    export OLLAMA_NUM_PARALLEL=2
    export OLLAMA_NUM_CTX=8192
    ...
    GPU_VRAM_MB=...  # then sets MAX_LOADED but NUM_PARALLEL stays 2
    ```
  - Backup:
    ```bash
    if [ -x "$VENV_DIR/bin/python" ]; then
        _POLICY_NP="$("$VENV_DIR/bin/python" -m backend.services.hardware_policy ollama_env ... | sed ...)"
    fi
    export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-${_POLICY_NP:-1}}"
    ```
    (Comment calls out the original CPU-offload bug from hardcoded 4/2.)

- **Ollama systemd drop-in rendering (around 1263-1279 current vs 1274+ backup):**
  - Current: Uses static `OLLAMA_DROPIN_SRC="$SCRIPT_DIR/scripts/ollama-systemd-dropin.conf"` directly (no rendering).
  - Backup: Renders to `data/ollama-dropin.rendered.conf` via `python -m ... hardware_policy ollama_env`, falls back to template only if empty, then uses the rendered as SRC. Installs the *rendered* version.

- **Model tier / bootstrap (around 1381-1410 current vs 1410-1430 backup):**
  - Current: Pure inline RAM/arch math from hardware.json or /proc/meminfo (llama3.2:1b for <=8GB/ARM else 8b). No policy call. "Model bootstrap: small-hardware tier..."
  - Backup: 
    ```bash
    _MODEL_TIER="$("$VENV_DIR/bin/python" -m backend.services.hardware_policy model_tier 2>/dev/null)"
    if [ -n "$_MODEL_TIER" ]; then
        ... from policy ...
        vader_info "Model tier from hardware_policy: ..."
    else
        # fallback inline
    ```
    (Note: policy CLI currently lacks "model_tier" handler — see below.)

- **End-of-boot verify (around 2168 backup; current end ~2156 has no equivalent):**
  - Current: No call whatsoever. Boot ends with log files / management instructions.
  - Backup (lines 2167-2170):
    ```bash
    # Advisory GPU-stack verification — never blocks boot.
    if [ -f "$SCRIPT_DIR/scripts/verify_gpu_stack.sh" ]; then
        bash "$SCRIPT_DIR/scripts/verify_gpu_stack.sh" || true
    fi
    ```
    (Immediately precedes "Log Files:" section.)

Other start.sh notes: Current still has some legacy `export OLLAMA_NUM_PARALLEL=2` at 1645. Backup start.sh also calls dep_reconciler more broadly in places, but core policy is in the shell paths above.

### install_pytorch.sh
- **Current:** `/home/llamax1/LLAMAX8/scripts/install_pytorch.sh` (386 lines).
- **Backup:** Identical content (byte-for-byte match on full read).
- Supports `--venv <path>` (or TARGET_VENV) + honors `GUAARDVARK_TORCH_CHANNEL` env (lines 229-234, 239):
  ```bash
  if [ -n "${GUAARDVARK_TORCH_CHANNEL:-}" ]; then
      CUDA_VERSION="$GUAARDVARK_TORCH_CHANNEL"
      ...
      vader_info "Torch channel from hardware_policy: $CUDA_VERSION"
  ```
- Full accelerator branching (Darwin MPS, AMD ROCm via rocm-smi + hardware.json, NVIDIA cu128/cu121/etc via compute_cap or policy override, CPU fallback).
- Strong swap-safety: uninstalls torch + nvidia-* + flash/xformers/pynvml/triton before reinstall.
- Post-torch verification prints (but no policy call inside; caller provides override).
- **Diff status:** Context said "modified in current" but observed identical to backup snapshot. Support for policy override + --venv is present and robust.

### setup_venv.sh files (audio_foundry + video_editor)
- **Current audio:** `/home/llamax1/LLAMAX8/plugins/audio_foundry/scripts/setup_venv.sh` (96 lines). Queries policy:
  ```bash
  TORCH_CHANNEL="$("$BACKEND_PY" -m backend.services.hardware_policy torch_channel ...)"
  ...
  TARGET_VENV=... GUAARDVARK_TORCH_CHANNEL="$TORCH_CHANNEL" bash "$INSTALL_PYTORCH" --venv ...
  ```
  Uses inline `build_main_venv` + music block. `verify_venv` + rebuild-on-fail + Chatterbox load check. `set -u`. Logs `[audio_foundry/setup_venv]`.
- **Backup audio:** Stronger version (116 lines). Has explicit `FATAL` guards:
  ```bash
  [ -n "$PLUGIN_DIR" ] && [ -d "$PLUGIN_DIR" ] || { echo "FATAL: PLUGIN_DIR unresolved" >&2; exit 1; }
  ... same for REPO_ROOT
  ```
  Separate `build_music_venv()` with per-step `|| { log "venv-music ... failed"; return 1; }`. More explicit failure logging on music venv build. Otherwise same policy query + override + verify + idempotent "healthy — skipping".
- **Video editor:** 
  - **Current:** **MISSING entirely**. `plugins/video_editor/scripts/` contains only `start.sh` + `stop.sh`. No setup_venv.sh.
  - **Backup:** `backups/.../plugins/video_editor/scripts/setup_venv.sh` (57 lines). Single-venv variant (per handoff note: "single venv variant").
    - Same FATAL dir guards as backup audio.
    - Queries same `TORCH_CHANNEL` via backend policy.
    - `verify_venv` (simple torch + .cuda() + print).
    - Builds, installs *all* requirements*.txt, calls `install_pytorch.sh --venv` with override.
    - Rebuild + verify or "DEGRADED", `exit 1` on fail (but advisory in context).
    - `set -u`, log prefix.

**Current video_editor/scripts/start.sh** (and backup's identical version): Uses its *own* `ensure_venv()` inline (requirements install + basic health, no torch policy, no --venv override, no setup_venv.sh call). Mirrors audio_foundry's start.sh which also does inline `ensure_venv()` (for its two venvs) + never invokes `setup_venv.sh`.

### scripts/ollama-systemd-dropin.conf
- **Current:** Static values (NUM_PARALLEL=1, no MAX_LOADED in some, etc.). Comments note "start.sh shell path defaults to 2".
- **Backup:** Updated header:
  ```
  # NOTE: As of 2026-06-14 start.sh RENDERS this drop-in from
  # backend.services.hardware_policy.ollama_tuning() (NUM_PARALLEL etc. derived
  # from detected VRAM). This file is the fallback used only when the policy
  # module isn't importable yet.
  ```
  Includes MAX_LOADED_MODELS=1. (Current dropin lacks the "RENDERED" note.)

---

## 2. Diffs + What Integration Is Present in Current start.sh vs. Missing

**Policy application to every env (the core claim):**
- **Backend torch via start.sh:** Partial remnant (install_pytorch.sh called) but **no policy-derived channel** (current start.sh + backend_venv reconciler both invoke bare `bash install_pytorch.sh`).
- **Ollama (NUM_PARALLEL + drop-in):** **Missing.** Hardcoded 2 (shell) + static dropin. Backup derives + renders.
- **Model tier selection:** **Missing.** Inline logic only.
- **Plugin isolated venvs (audio + video) torch:** setup_venv.sh *scripts* (for audio) exist and do the right query+override+verify, but:
  - Never auto-called by `./start.sh`, plugin `start.sh`s, or (current) dep_reconciler.
  - Current dep_reconciler has `TorchVenvDetector` (detect-only warnings, points to manual `plugins/.../setup_venv.sh`).
  - No `isolated_plugin_venv.py` reconciler (present only in backup snapshot; would have used `policy_fingerprint()` for rebuild-on-hw-change + actively run the setup scripts for enabled plugins).
  - `registry.py` *does* detect `setup_venv.sh` for "isolated" classification (current code).
  - Video setup_venv.sh **absent** in current tree.
- **End-of-boot verify:** Call + script both **absent**. (Handoff explicitly: "verify call may be there but script absent!" — here *both* missing.)

**In current start.sh bootstrap (ensure_backend_venv):**
- Still does full requirements + optional CV + bare install_pytorch + post-pins + flash purge + limited `dep_reconciler --only backend_venv,cli_venv`.
- Dep reconciler registry always adds TorchVenvDetector (but only warns for isolated plugins that have setup_venv.sh).

**Result:** The "one hardware policy → every environment, applied + verified by `./start.sh`" is **NOT achieved** in the current tree. Remnants of the *supporting* code (install_pytorch override support, audio setup_venv script, policy module + detector, dropin template) exist, but the *wiring* (policy calls in start.sh paths, verify, rendered dropin, active reconciler for plugins, video script) is absent or drifted.

---

## 3. Robustness Evaluation

- **Error handling:** 
  - Verify (backup): Advisory only (`|| true`, `exit 0` always). Degraded states collected + written to `data/gpu_stack_status.json`. Non-fatal logs ("⚠", "DEGRADED").
  - setup_venv (both): Rebuild attempts once; on final fail logs "DEGRADED" + specific advice (e.g. "rely on Kokoro fallback"), exits 1 (non-blocking for caller).
  - install_pytorch: `set -e` but many `|| true` on uninstalls/purges. Warns but continues on most errors. Strong pre-venv guards.
  - start.sh: `vader_warn` on failures; continues boot for non-core (torch, ollama pull, etc.).
  - Good: never lets a bad torch poison the whole boot.

- **Idempotency:**
  - Verify: Checks presence + kernel test; no side effects.
  - setup_venv (audio/video): `if verify_venv ...; then log "healthy — skipping"; else rebuild...`. Re-runnable safely.
  - install_pytorch: Detects via nvidia-smi/compute_cap (or policy override); uninstall+force-reinstall is the swap safety.
  - Backend reconciler + start paths: Timestamps/sentinels (`.deps_installed`, `.guaardvark_bootstrap_ts`).
  - Drop-in: `cmp -s` guard before reinstall.

- **TMPDIR handling:** **None.** No `TMPDIR=.../data/piptmp` or equivalent in install_pytorch.sh, setup_venv.sh, start.sh, or reconcilers. (See open follow-ups.)

- **Rebuild-on-hw-change:** 
  - Intended: `policy_fingerprint()` (in hardware_policy.py) folded into isolated reconciler `compute_hash()`.
  - Current: Only in backup's missing `isolated_plugin_venv.py`. TorchVenvDetector is passive. Backend_venv compute_hash only hashes requirements (no policy fingerprint, no gpu_uuid in some paths).
  - hardware_policy has `_is_stale_profile` + compute_cap guard (good).

- **Logging of degraded states:** Strong in intended verify (json + console) and setup_venv ("DEGRADED:" lines). Current start.sh / reconcilers use `vader_warn` + setup.log / preflight. No centralized gpu_stack_status in current.

- **Other robustness:**
  - install_pytorch: Excellent swap safety + multi-accelerator (MPS/ROCm/NVIDIA/CPU) + post-purge of flash/xformers/pynvml.
  - FATAL path guards stronger in backup setup_venvs.
  - start.sh has CI/Codex early exit, FAST_START guards, etc.
  - Risk: `set -u` + `rm -rf "$venv"` after cd guards (present in backup setup).

---

## 4. video_editor setup_venv Completeness
- **Backup version is complete** for "single venv variant per handoff":
  - FATAL guards on PLUGIN_DIR/REPO_ROOT.
  - Policy query for TORCH_CHANNEL (via backend python -m ...).
  - Calls `install_pytorch.sh --venv "$venv" GUAARDVARK_TORCH_CHANNEL=...`.
  - Idempotent verify (torch + cuda kernel).
  - Rebuild path (rm -rf, venv, pip reqs* + torch override, re-verify).
  - DEGRADED logging + exit 1 on fail (advisory per context).
  - Single venv (unlike audio's two).
- **Current tree:** Absent (video_editor/scripts/ only start/stop). The existing video_editor/venv appears to be a basic one (no evidence of policy torch in its installed packages from dir summary). Its start.sh uses a non-policy inline ensure_venv.

---

## 5. Current vs. Backup Drift That Could Re-Introduce Original Bug
**Yes — multiple paths that re-introduce "backend-only torch + hardcoded NUM_PARALLEL".**

- **NUM_PARALLEL not derived:** Current start.sh hardcodes 2 (shell path + exports). Ollama tuning comment acknowledges old 4->1 but code didn't update the derivation. Backup derives from `ollama_env` (policy yields 1 on 16 GB card).
- **Torch not forced for plugins:** 
  - audio_foundry has setup_venv.sh (current version simplified) but its `start.sh` + main start.sh never call it (use inline ensure_venv that pulls torch from requirements pins, which are wrong for Blackwell/sm_120).
  - video_editor: no setup_venv.sh at all; start.sh inline ensure_venv has same problem.
  - dep_reconciler: TorchVenvDetector warns only ("run .../setup_venv.sh manually"); no active reconciler present. Registry detects isolated mode but build_active_reconcilers does not invoke per-plugin setup.
- **Backend torch path:** start.sh bootstrap + backend_venv.py both call bare install_pytorch.sh (no GUAARDVARK_TORCH_CHANNEL=). Backup start.sh wires it (reconciler path still bare even in backup).
- **Drop-in:** Static in current (NUM_PARALLEL baked); rendered policy version only in backup start.sh.
- **Model selection:** No policy; inline only. (Also: policy.py CLI lacks `model_tier` handler, so even if wired it would fail with "unknown key". Policy supports it internally for fingerprint.)
- **verify + end gate:** Absent → no post-boot catch for degraded torch (as happened mid-session per handoff: uninstall step left torch-less venv).
- **Reconciler call in start.sh:** Limited to backend/cli only; does not pull in torch_venv_detector fully for provisioning.

Original bug ("hardware-correct provisioning applied to 1 of N environments") is re-introduced by the missing wiring.

---

## 6. Review of Open Follow-ups (from backups/SESSION_HANDOFF_06-14-2026.md)
1. **/tmp 8GB tmpfs risk (use data/piptmp):** **Not addressed.** No `TMPDIR`, `export TMPDIR=.../data/piptmp`, or equiv in install_pytorch.sh, any setup_venv.sh, start.sh, or reconcilers. Handoff notes video_editor rebuild hit ENOSPC. (data/ dir exists; piptmp not created/used.)
2. **Don't Ctrl-C install_pytorch mid-uninstall:** **Not addressed.** No comments, traps, or docs in the script or callers warning "interrupted run leaves the venv torch-less (post-uninstall pre-reinstall)". Handoff notes it was root cause of a mid-session breakage caught only by (now-missing) verify.
3. **Other latent (AMD/legacy, old compute_cap skip in policy override):** Still present in current install_pytorch.sh (GUAARDVARK_TORCH_CHANNEL honored only inside NVIDIA branch after compute_cap parse; AMD branch ignores it and uses ROCM_WHL separately).
4. Handoff notes "Not committed", plugins disabled in state, etc. — consistent with observed state (audio/video venvs present but provisioning scripts not fully live).

---

## 7. Provisioning Completeness + Risks Summary

**Is "applied + verified by ./start.sh" to every environment achieved in current tree?** **No.**

- **What works today (remnants):** 
  - install_pytorch.sh is policy-*ready* (honors override + --venv; multi-arch; strong cleanup).
  - audio_foundry/scripts/setup_venv.sh exists and does the right thing (policy query + override + verify).
  - hardware_policy.py + detector + registry classification for isolated venvs exist.
  - ollama dropin template + some tuning logic.
  - Dep reconciler invoked (limited) during backend bootstrap.
  - Venv health guards in start.sh/plugin starts (cross-machine shebang checks).

- **What's missing / broken for the claim:**
  - `scripts/verify_gpu_stack.sh` + end-of-boot call: 100% absent.
  - Policy calls in start.sh (torch_channel for backend, ollama_env derivation + rendered dropin, model_tier): absent (current uses pre-fix hardcoded/inline paths).
  - video_editor/scripts/setup_venv.sh: absent.
  - No automatic invocation of audio (or video) setup_venv.sh from boot/plugin start/dep_reconciler active path. Inline ensure_venv paths in plugin start.shs bypass policy entirely.
  - IsolatedPluginVenv active reconciler: absent (only passive detector).
  - Backend torch provisioning paths (start + reconciler) do not pass GUAARDVARK_TORCH_CHANNEL.
  - NUM_PARALLEL still hardcoded (re-introduces original 16 GB CPU-offload risk).
  - No TMPDIR/piptmp or interrupt-safety in torch paths.
  - Policy CLI incomplete for model_tier (would break if start.sh wiring were restored).

**Risks:**
- Re-introduction of original symptoms (slow CPU torch in plugins, Ollama offload on 16 GB cards, post-restore wrong torch variant, no end-of-boot visibility into degraded GPU stack).
- Manual-only fix for plugin venvs (users must run setup_venv.sh after enable or hw change).
- Large wheel installs can still OOM/ENOSPC on /tmp tmpfs.
- Interrupted `install_pytorch.sh` (or setup callers) leaves broken venvs with no torch (uninstall step is first).
- Drift between shell start.sh paths, dep_reconciler, and plugin start.shs.
- Policy fingerprint / rebuild-on-hw not active for plugins (detector only logs warnings).
- Inconsistent with handoff-verified state ("GPU stack: healthy" via verify + policy-derived 1 for NUM_PARALLEL + cu128 everywhere).

**Recommendations (to achieve the stated goal):**
- Restore `scripts/verify_gpu_stack.sh` from backup.
- Wire the 4 policy call sites + verify call + rendered dropin logic into current start.sh (use backup as source).
- Add `plugins/video_editor/scripts/setup_venv.sh` (copy from backup).
- Update backend_venv.py + start.sh torch calls to pass `GUAARDVARK_TORCH_CHANNEL=...`.
- Either restore isolated_plugin_venv reconciler (and call setup scripts for enabled isolated plugins) or wire explicit calls in start.sh plugin bootstrap section.
- Add `model_tier` case to hardware_policy.py:main() (and update CLI docs).
- Implement TMPDIR=.../data/piptmp (mkdir -p) + docs in install_pytorch + setup_venvs + start.sh.
- Add prominent "DO NOT INTERRUPT" + trap cleanup comments in install_pytorch.sh.
- Ensure main start.sh invokes full reconcilers (including torch_venv_detector) or explicitly runs setup for audio/video when enabled.
- Update current audio setup_venv.sh to match backup's stronger guards + separate build_music.
- Add `data/gpu_stack_status.json` consumption to health/UI layers.
- Re-run full test matrix post-wiring (test_hardware_policy, isolated reconciler tests, dep_reconciler suite).
- Consider making setup_venv.sh the *only* path for isolated (remove duplication in plugin start.sh ensure_venv).

**Absolute paths referenced:**
- Current start.sh: /home/llamax1/LLAMAX8/start.sh
- Backup snapshot root: /home/llamax1/LLAMAX8/backups/big-fing-refactor__06-14-2026___20260614_205731/
- Missing verify: would be /home/llamax1/LLAMAX8/scripts/verify_gpu_stack.sh
- Policy: /home/llamax1/LLAMAX8/backend/services/hardware_policy.py
- Audio setup (current simplified): /home/llamax1/LLAMAX8/plugins/audio_foundry/scripts/setup_venv.sh
- Video start (bypass): /home/llamax1/LLAMAX8/plugins/video_editor/scripts/start.sh
- install_pytorch: /home/llamax1/LLAMAX8/scripts/install_pytorch.sh
- Dropin: /home/llamax1/LLAMAX8/scripts/ollama-systemd-dropin.conf
- Reconciler registry/detector: /home/llamax1/LLAMAX8/scripts/dep_reconciler/...
- Handoff: /home/llamax1/LLAMAX8/backups/SESSION_HANDOFF_06-14-2026.md
- Report location: /home/llamax1/LLAMAX8/regroup-reports/provisioning-reviewer.md

**Status:** Detailed diffs, completeness analysis, and risks documented. The provisioning fix is partially implemented in supporting scripts but the boot integration ("applied + verified by ./start.sh") has not landed in the current tree.

---
*Report generated by Provisioning Scripts & Boot Integration Reviewer task. All file reads performed via tools on 2026-06-14.*