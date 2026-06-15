# Team Regroup Before Merge — 2026-06-14

**Role:** Merge Readiness & Regroup Lead / Coordinator  
**Workspace:** /home/llamax1/LLAMAX8 (on main, dirty working tree)  
**Sources (full reads):**  
- regroup-reports/hardware-policy-specialist.md (full)  
- regroup-reports/provisioning-reviewer.md (full)  
- regroup-reports/dep-reconciler-expert.md (full)  
- regroup-reports/tests-verification-analyst.md (full)  
- backups/SESSION_HANDOFF_06-14-2026.md (full)  
- Canonical backup snapshot: backups/big-fing-refactor__06-14-2026___20260614_205731/ (key files read for diffs: hardware_policy.py, start.sh excerpts, verify_gpu_stack.sh, isolated_plugin_venv.py + registry, video_editor/scripts/setup_venv.sh, test_hardware_policy.py, test_isolated_plugin_venv_reconciler.py)  
- Current tree (via list_dir + read_file + grep): backend/services/hardware_policy.py, hardware_detector.py, start.sh, scripts/install_pytorch.sh, plugins/audio_foundry/scripts/setup_venv.sh, plugins/video_editor/scripts/start.sh, scripts/dep_reconciler/registry.py + reconcilers/* + detectors/torch_venv.py, backend/tests/test_hardware_policy.py + test_hardware_detector.py + dep_reconciler tests, scripts/ollama-systemd-dropin.conf, data/plugin_state.json, .git/HEAD + partial logs, CLAUDE.md, INSTALL.md, and supporting (gpu_resource_policy.py, hardware_service.py, etc.)  
- Git context per prompt + confirmed reads (HEAD=main; multiple modified files including start.sh, detector, install_pytorch, plugin scripts, dropin, frontend, services, docker-compose, INSTALL.md, etc.; untracked described as hardware_policy.py + test + audio setup remnants + tests/docs at top level in initial dirty description).  

**Orchestrator verification context (per tests-verification-analyst.md + handoff):** "33 tests pass on reduced policy tests"; missing verify_gpu_stack.sh / video_editor/setup_venv.sh / isolated_plugin_venv.py + test; "policy CLI partially works but no VULKAN"; "24+9 tests vs claimed 26+..."; start.sh remnants but not full policy wiring.

---

## Executive Briefing (Tight)

**The "big-fing-refactor" (one stdlib hardware_policy.py as single source of truth for torch_channel / ollama_tuning / model_tier / policy_fingerprint, applied + verified by start.sh to every env including isolated plugins, with dep_reconciler tracking + end-of-boot verify) is PARTIALLY LANDED but INCOMPLETE + DRIFTED in the current main tree vs the canonical backup snapshot + SESSION_HANDOFF_06-14-2026.md verified state.**

- **What delivered in backup/handoff (SESSION_HANDOFF:38-66, tests-verification-analyst:10-18, provisioning-reviewer:178):** Full policy (VULKAN=0 forced for nvidia in ollama_tuning + model_tier CLI + fingerprint); start.sh wired for policy torch/ollama_env/model_tier + rendered dropin + verify call at end (2168); audio + video setup_venv.sh (both call policy + override install_pytorch); isolated_plugin_venv.py reconciler (folds policy_fingerprint into compute_hash + manifests for hw-drift rebuilds); registry registers it; 26+9+4 hardware/isolated tests + dep_reconciler (91); verify_gpu_stack.sh (advisory, writes data/gpu_stack_status.json, always exit 0); GPU stack healthy on the 16GB Blackwell box (NUM_PARALLEL=1, cu128 everywhere, no CPU offload).
- **What is actually in current main (hardware-policy-specialist:10-20, provisioning-reviewer:162-178, dep-reconciler-expert:10-20, tests-verification-analyst:19-31):** Policy present at backend/services/hardware_policy.py but **stripped** (no VULKAN:84 (ollama_tuning), main() only torch_channel|ollama_env|fingerprint:153-174, no model_tier); tests reduced (24 policy + 9 detector + 0 isolated vs claimed 26+9+4; relaxed model_tier assert at test:100 `!= "llama3.2:1b"`; no VULKAN tests); start.sh calls detector only (1046,1526,1534) + hardcodes NUM_PARALLEL=2 (1239-1240,1645) + inline model dupe (1359-1389, json parse + if) + static dropin copy (1263); bare install_pytorch calls (no GUAARDVARK_TORCH_CHANNEL from policy); audio setup_venv present (calls policy:17) but **not invoked** by start/plugin starts/dep; video_editor/scripts/ has only start/stop (no setup_venv.sh; its start.sh uses inline ensure_venv:35-73 bypassing policy); dep_reconciler has classify (registry:12-23, tested) + TorchVenvDetector (detect-only warnings) but **no IsolatedPluginVenv registration or file** (registry:78-85 returns only 6, no import/listcomp; no fp folding in any compute_hash); verify script + call + gpu_stack_status.json 100% absent; policy_fingerprint/_is_stale/_load only internal (policy:103,118,135; unused in reconcilers/start per specialist:115-119, dep-expert:15,228). 33 tests green on reduced subset (analyst:140 sim); dep tests 77 functions (vs 91 claimed).
- **Drifts/missing vs backup canonical (cross refs in reports + direct reads):** provisioning-reviewer:228-242 lists 5+ re-intro paths (NUM hardcoded, no plugin torch force, no backend policy channel, no verify, no rendered dropin, model inline dupe, policy CLI would fail on model_tier); dep-expert:15 ("compute_hash / manifests in no current reconciler calls hardware_policy.policy_fingerprint"); tests-analyst:174 ("partial green on subset... cannot re-run exact handoff commands").
- **Open items from handoff (SESSION_HANDOFF:124-142) unaddressed in current:** /tmp 8GB tmpfs (no TMPDIR=.../data/piptmp in install/setup/start); interrupt safety (no "DO NOT Ctrl-C" warnings/traps in install_pytorch; uninstall-first step leaves torch-less); AMD latent (install honors TORCH_CHANNEL only in NVIDIA branch after compute_cap; start.sh nvidia-smi biased); plugins disabled in plugin_state.json (audio/video user_enabled:false); CLAUDE.md stale (repo path/git claim; refreshed 2026-06-02 but still claims git + /home/llamax1/LLAMAX8 vs handoff's GX1 note); no design docs (handoff refs docs/superpowers/... absent in current tree).

**Bottom line:** The refactor heart (policy + detector compute_cap + audio script + install override + classify + stale guard) has **supporting code remnants** but the **wiring/integration/verification** that made it "live everywhere + verified" (start.sh, registry, isolated reconciler + fp, verify, full tests, VULKAN/model_tier) did **not land**. Current is a **regression** from the handoff's "GPU stack: healthy" state. High risk of re-introducing the original CPU torch / 4-parallel on 16GB / silent Vulkan / stale hw / inconsistent state bugs.

**Git context (user prompt + confirmed):** On main (ref: refs/heads/main), dirty working tree (modified: start.sh:1239 hardcodes + 1359 inline + 848 bare install + 1263 static dropin; install_pytorch.sh; hardware_detector.py (compute_cap present); many plugin jsons/start.shs; frontend pages; services (e.g. gpu_resource_policy.py unrelated); docker-compose; INSTALL.md; ollama dropin (has VULKAN=0 but comment drift "start.sh ... defaults to 2"); + untracked per initial description: hardware_policy.py/test + audio setup_venv remnants + tests/docs at top level — though current backend/services/ + tests/ hold the partials). 24 ahead / 9 behind origin/main. Recent commits mixed "team:/agentic" (termination budget, redis reconnect, voice streaming, bare excepts, edge graceful, RAG, music-video, infra fixes per prompt) + others (rag-embedding-refactor merges, plugin GPU modal, etc. from .git/logs/HEAD). Partial refactor "landed" amid unrelated work; dirty tree mixes refactor remnants with other changes.

**Risks if merge/push main now (per all reports):** Re-introduce original bugs (CPU torch in plugins via bare reqs pins + no setup_venv invocation; NUM_PARALLEL=2/4 on 16GB forcing offload per handoff root cause; wrong channel on Blackwell without policy override in main paths; silent Vulkan fallback; stale hw cache without _is_stale guard + fp in reconcilers); no hw-aware rebuilds for plugins (fp absent in hashes; only TorchVenvDetector passive warnings); no end-of-boot visibility (verify + gpu_stack_status missing → mid-boot torch wipe like handoff's install kill would go undetected); inconsistent state across start/reconciler/plugin paths (detector vs policy; inline dupe vs model_tier fn; static vs rendered dropin); test confidence **lower than handoff** (33 on reduced vs claimed 39+91 green; missing VULKAN/isolated/verify means regressions invisible); carrying incomplete feature + unrelated team changes amplifies drift on origin/main; plugins disabled + CLAUDE.md stale + open handoff items compound support risk. "The 'one hardware policy → every environment' is NOT achieved" (provisioning-reviewer:178, hardware-policy-specialist:129).

**Recommendation:** **DO NOT merge/push main as-is.** Create clean feature branch from backup canonical (or port), complete the wiring (policy restore + start.sh + registry + isolated + verify + video script + tests + opens), clean/stage tree (separate refactor from other mods if possible), full re-verify (policy CLI + pytest hardware+dep+isolated + bash verify + start dry paths), update docs (CLAUDE.md + design note), then merge. Prioritized plan below.

**Status:** Regroup view + report complete. Ready for user decision on merge path.

---

## 1. Synthesized "Team Regroup Before Merge" View: Backup Snapshot + Handoff Delivered vs Actual Current Main Tree

**Delivered by backup/handoff (canonical per SESSION_HANDOFF_06-14-2026.md:38-99 + specialist reports describing "the state containing the intended fix"):**

- New core: backend/services/hardware_policy.py (full: torch_channel, ollama_tuning **with VULKAN**, model_tier, policy_fingerprint, _is_stale_profile, _load_hardware, main() with torch_channel|ollama_env|**model_tier**|fingerprint). See backup read:50-89 (VULKAN:69,82,88), 158-183 (model_tier CLI + tab print).
- Detector: hardware_detector.py enhanced with compute_cap in nvidia probe (query "name,memory.total,driver_version,compute_cap").
- start.sh: Full policy calls (e.g. GUAARDVARK_TORCH_CHANNEL=... python -m ... torch_channel wrapping install_pytorch:851-853 backup; ollama_env parse for NUM + rendered dropin from ollama_env output:1248-1289; model_tier call:1414-1418; detector early + ensure; advisory verify at end:2168-2170 `bash ... || true`).
- Plugin scripts: audio_foundry/scripts/setup_venv.sh (policy query + GUAARDVARK_TORCH_CHANNEL + dual venvs + Chatterbox verify + rebuild); **video_editor/scripts/setup_venv.sh** (single-venv variant with FATAL guards + policy + verify).
- Dep: scripts/dep_reconciler/reconcilers/isolated_plugin_venv.py (Reconciler subclass; manifests=setup+reqs*; is_active=setup present; compute_hash folds hash_file + hardware_policy.policy_fingerprint(_load_hardware) for hw-drift; install runs the bash setup_venv (returns 0 even on fail for non-blocking)); registry.py updated (import + isolated_plugins listcomp via classify + append *[Isolated... for pid] after TorchVenvDetector).
- Tests: test_hardware_policy.py (26: extra VULKAN-specific e.g. test_ollama_tuning_nvidia_forces_cuda_over_vulkan asserting VULKAN=0; test_ollama_tuning_amd_keeps_vulkan=1; test_ollama_tuning_no_gpu... includes VULKAN=0; stricter model_tier_standard == "llama3.1:8b"; full torch/ollama/model/stale/load/fp); test_hardware_detector.py (9, incl nvidia_probe_includes_compute_cap); **new backend/tests/test_isolated_plugin_venv_reconciler.py** (4: is_active true/false on setup_venv, hash stable + changes on fp monkeypatch); dep_reconciler/ tests (91 passed claimed).
- Verify: scripts/verify_gpu_stack.sh (51 lines; checks 4 venvs for torch.cuda kernel + ollama ps !%CPU; writes data/gpu_stack_status.json {"degraded":bool,"components":[]}; always exit 0; advisory only). Wired + "GPU stack: healthy" verified on box (NUM=1, cu128, no offload).
- Other: install_pytorch honors override (identical); ollama dropin updated header (references policy render + MAX_LOADED); audio stronger in backup (FATAL PLUGIN_DIR/REPO_ROOT guards + separate build_music_venv with per-step error); dep state tracks enabled (plugins disabled in plugin_state); policy_fingerprint used for rebuild triggers.
- Verification cmds (SESSION_HANDOFF:104-120): exact pytest lines for 26+9+4 + dep; bash verify; policy CLIs (torch_channel→cu128, ollama_env→6 lines incl NUM=1 + VULKAN, model_tier→8b<tab>nomic); manual setup_venv re-runs.
- Real fixes on box (handoff:69-79): audio/video/backend venvs to cu128 + GPU verified (Chatterbox loads); NUM=1 (was 4); stale hardware.json self-healed; all green.

**Actually in current main tree (confirmed by direct read_file/grep/list_dir on /home/llamax1/LLAMAX8/ vs backup extraction; reports full analysis):**

- Policy at /home/llamax1/LLAMAX8/backend/services/hardware_policy.py (180 lines current read): torch_channel:26-47, ollama_tuning:50-84 **no VULKAN key** (returns 5 items; 72-77 vram logic for 1/2), model_tier:87-100, fp:103-115, _is_stale:118-132 (nvidia-only for compute_cap), _load:135-150, main:153-174 **docstring + cases only torch_channel | ollama_env | fingerprint** (no model_tier; would print "unknown key" on start.sh call). (hardware-policy-specialist:35-37,85-93; confirmed exact read.)
- Detector: /home/llamax1/LLAMAX8/backend/services/hardware_detector.py:136-158 (_probe_gpu_nvidia includes "compute_cap" query + parse; AMD rocm vram best-effort unverified docstring 166-168). Good enhancement present.
- start.sh (~2157 lines): Detector calls only (1046 early --output hardware.json; 1520 ensure_hardware_profile; 1549 post-venv). Ollama: hardcodes `export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-2}"` (1239-1240) + later unconditional =2 (1645) + nvidia-smi guard for KV/flash (no policy ollama_env derivation). Dropin: static copy of scripts/ollama-systemd-dropin.conf (1263-1265; no render). Model bootstrap: inline RAM/arch from hardware.json or /proc (1364-1389 python -c json + if <=8/arm →1b else 8b; dupe of model_tier; no `python -m ... model_tier`). Backend torch: bare `bash .../install_pytorch.sh` (848-849; no GUAARDVARK_TORCH_CHANNEL= policy wrap). No verify call at end (ends 2156 with log instructions). (provisioning-reviewer:39-103,228; confirmed grep + reads on current.)
- Audio: /home/llamax1/LLAMAX8/plugins/audio_foundry/scripts/setup_venv.sh present (96 lines; policy torch_channel:17; passes to install with override; verify_venv + build_main + music block + Chatterbox snippet; set -u; logs; idempotent). **But never auto-called** (video_editor start + main start + dep use inline). (Current simplified per provisioning:122-128 vs backup stronger FATALs.)
- Video: /home/llamax1/LLAMAX8/plugins/video_editor/scripts/ **only start.sh + stop.sh** (no setup_venv.sh). start.sh ensure_venv inline (35-73: venv create + pip reqs + sentinel; **no policy, no TORCH_CHANNEL, no install_pytorch --venv override**, no torch kernel verify beyond basic). (provisioning-reviewer:135-146,224.)
- Dep: /home/llamax1/LLAMAX8/scripts/dep_reconciler/registry.py (86 lines): classify_plugin_venv_mode present + correct (12-23: setup_venv.sh or venv-* → isolated; used to filter shared_plugins:69-74). enabled_plugin_ids full (handles user_enabled). **build_active_reconcilers (50-85) returns only [BackendVenv, Alembic, PluginBundle, Frontend, CliVenv, TorchVenvDetector]** (no import of isolated, no isolated_plugins listcomp, docstring lists 6). (dep-reconciler-expert:49-95, confirmed full read.) 
  - Reconcilers: no isolated_plugin_venv.py (only alembic/backend_venv/cli_venv/frontend/plugin_bundle). TorchVenvDetector (detectors/torch_venv.py:10-): detect-only warnings "run .../setup_venv.sh" (no hash/install/state). No policy_fingerprint calls anywhere in dep_reconciler/ (grep zero; backend_venv compute_hash only reqs manifests + extra gpu_uuid/numpy; no fp in decisions). (dep-expert:15,225-248; specialist:115-119.)
  - No test_isolated_plugin_venv_reconciler.py in backend/tests/ (or dep_reconciler subdir).
- Tests: test_hardware_policy.py (24 def test_): torch_channel 10x, ollama 4x (16gb/24gb/no_gpu/zero_vram; **no VULKAN asserts**; 24gb lacks KV; no_gpu lacks VULKAN; model_tier 4x incl relaxed test_model_tier_standard_for_normal_box:100 `assert t["chat"] != "llama3.2:1b"` (not exact 8b); 3 stale, 2 load, 1 fp. (tests-analyst:39-51, confirmed grep; specialist:51,104.) test_hardware_detector.py:9 (exact, incl compute_cap). Dep: 77 test functions across 16 files (test_registry has classify tests:9-27; test_torch_venv_detector etc.; no isolated). run_tests.py / CI / quality_gate / preflight / release do not specially invoke hardware/verify/isolated (analyst:197-215).
- Verify: scripts/verify_gpu_stack.sh **ENTIRELY MISSING** (grep 0 in current tree; only in backup + handoff + reports). No data/gpu_stack_status.json. No end-of-boot call.
- Dropin: /home/llamax1/LLAMAX8/scripts/ollama-systemd-dropin.conf (static NUM=1 + VULKAN=0 + comments noting "start.sh shell path defaults to 2" at 31-32 + "reconcile if you change this"; has "leaving Vulkan enabled only adds a silent fallback path").
- Install_pytorch: honors GUAARDVARK_TORCH_CHANNEL (229-233: "Torch channel from hardware_policy"; single-source comment) + --venv + full branches (NVIDIA after cap parse; AMD separate). Identical to backup per provisioning:108-119. But **not passed from main start.sh/backend_venv paths**.
- Plugin state: data/plugin_state.json (user_enabled audio_foundry:false, video_editor:false, ollama:true; quarantined + failure counts for some). (dep-expert:196-212.)
- Other: CLAUDE.md (stale per handoff note + prompt: repo/git/path); no docs/superpowers/specs/2026-06-14-... (handoff design refs absent); gpu_resource_policy.py (unrelated GPU reclaim compose; design in local-workspace-only); hardware_service.py (legacy torch training recs; no overlap). (specialist:21,67-68,131-134.)
- "Orchestrator" / verification output context: Per tests-verification-analyst:138-165 sim: current 24+9 would pass (33 green, aligned to reduced impl); full handoff tests would fail (import missing isolated, KeyError VULKAN, model_tier CLI unknown, verify cmd not found); dep 77 (not 91); policy CLI: torch/ollama work (but no VULKAN output), model_tier fails.

**Completeness summary (cross reports):** Supporting pieces (policy skeleton + fp/stale/load/detector cap + audio script + classify + install override + some tests + dropin VULKAN comment) present but **not live**. "The feature heart ... is partially landed ... but incomplete and drifted" (hardware-policy-specialist:10). "Is 'applied + verified by ./start.sh' to every environment achieved? **No.**" (provisioning:256). "The 'plugin isolation' feature ... is **not present**" (dep:10). "Significant test + verification drift. The full test suite + verify logic ... is NOT present" (tests:18). "Current is a regression from backup snapshot's fuller wiring" (specialist:20).

**Line/path refs (examples; see reports for 100+):** 
- Current policy no VULKAN: /home/llamax1/LLAMAX8/backend/services/hardware_policy.py:84 (ollama_tuning return); main:156 (docstring).
- Current start hardcode: start.sh:1240 (`NUM_PARALLEL:-2`), :1645, :1383 (inline model if).
- Current registry: /home/llamax1/LLAMAX8/scripts/dep_reconciler/registry.py:84 (only TorchVenvDetector).
- Missing: scripts/verify_gpu_stack.sh; plugins/video_editor/scripts/setup_venv.sh; scripts/dep_reconciler/reconcilers/isolated_plugin_venv.py; backend/tests/test_isolated_plugin_venv_reconciler.py.
- Backup canonical: backups/big-fing-refactor__06-14-2026___20260614_205731/backend/services/hardware_policy.py:69 (VULKAN), :176 (model_tier case); start.sh:851 (policy torch wrap), :2168 (verify), :1289 (ollama_env render); registry:95 (isolated listcomp); test_isolated:13-47 (4 tests with fp monkeypatch).
- Reports: hardware-policy-specialist:14 (drift list), :174 (start.sh:1239); provisioning:164 (drift), :201 (fp absent); dep:15 (absent folding), :38 (intended hash); tests:140 (33 sim), :174 (partial green), :317 (backup test path).

---

## 2. Cross-Cut Findings

**Policy heart (VULKAN, model_tier CLI, fingerprint usage) — hardware-policy-specialist + tests-analyst + reads:**
- VULKAN: Absent in current ollama_tuning (no key in any return path; nvidia/amd/no-gpu degrade all lack it). Backup forces 0 for nvidia (to prevent silent Vulkan fallback despite CUDA-capable sm_120; "leaving Vulkan enabled only adds a silent fallback path" in dropin). Current tests have zero VULKAN coverage (no test_ollama_tuning_nvidia_forces... or amd_keeps... or no_gpu VULKAN assert). Dropin has VULKAN=0 hardcoded but policy is source of truth per design — drift. (specialist:14,86-87; tests:44-50; policy read:78-84 vs backup:69,81-88.)
- model_tier CLI: Policy fn exists + tested (current relaxed assert); but main() lacks case (would hit "unknown key"). Backup main:176-179 prints "chat<tab>embed". start.sh model bootstrap uses dupe inline (never calls policy). (specialist:14,88-89,174; tests:49-50,98-101 (relaxed); start read:1383; backup start:1414.)
- Fingerprint usage: Correct impl in policy (decisions only: torch|ollama_np|tier sha; stable/sensitive per test:143-149 current). _is_stale_profile + _load_hardware correct (guards nvidia missing compute_cap; added 2026-06-14). **But not used outside policy/tests/_load**: zero in dep_reconciler (no manifests/compute_hash), start.sh, cuda_config, node_api, etc. Intended "folded into isolated reconciler compute_hash()". (specialist:115-119 "Not wired"; dep:15,228 "The folding is absent"; provisioning:200-202; policy:103-115,118-150.)
- Other: AMD/legacy solid in policy/detector (rocm, vram ollama degrade, arm tier, cpu safe); gaps in integration/verification/start bias. (specialist:22-27.)

**Provisioning/boot (start.sh wiring, verify missing, video setup missing, NUM_PARALLEL hardcodes) — provisioning-reviewer + handoff + reads:**
- start.sh: Partial remnants (install called, detector, some ollama tuning guard, dropin install) but **core wiring absent** (no policy calls for torch_channel/ollama_env/model_tier/fingerprint; hardcoded 2; inline model dupe from hardware.json; static dropin not rendered; no GUAARDVARK_TORCH_CHANNEL export in backend path; no verify). Backup has explicit policy authority comments + calls + render. (provisioning:39-103,161-178; start grep:1239,1359,848; backup start:851,1248,1414,2168.)
- verify: 100% absent (script + call + data/ json). Backup 51-line advisory (checks 4 venvs + ollama ps CPU%; exit 0; healthy json). (provisioning:13-31,172; tests:113-115,176-193; backup verify read full.)
- Video setup: Absent (scripts/ only start/stop; start.sh inline ensure_venv bypasses). Backup: 57-line single-venv with FATAL guards, policy query, install override, verify, DEGRADED on fail. (provisioning:135-146,215-225; video start read:35-73.)
- NUM_PARALLEL: Current start hardcodes 2 (shell + export 1645) + nvidia-smi path; dropin=1 but comment acknowledges mismatch. Policy derives 1/2 from vram ( >=20000 →2; 16GB →1 fix for offload bug). (provisioning:53-74,231; handoff:29-31,77 root cause.)
- Backend/plugin paths: Bare calls or inline; audio script exists but dormant (not called; dep detector only warns). install_pytorch ready but not fed policy channel from main flows. (provisioning:162-172,229-242.)
- Robustness: Good in pieces (idempotent setup, install swap safety via uninstall+reinstall, || true guards); missing: TMPDIR/piptmp (handoff open; video hit ENOSPC), interrupt warnings (uninstall-first leaves torch-less), end-of-boot gate. (provisioning:182-212,246-251.)

**Dep_reconciler (classify partial, no active isolated reconciler or fp in hashes) — dep-reconciler-expert + reads:**
- classify: Landed + tested (registry:12-23 "setup_venv.sh OR venv-*"; test_registry:9-27 covers audio_foundry/lora/diskord cases; used to exclude from PluginBundle). (dep:49-109,174-182.)
- Registration: Missing (build_active_reconcilers docstring + return lists 6 only; no Isolated import or `[IsolatedPluginVenv(repo_root, pid) for ...]`; enabled filtered but isolated list absent). Backup registry has full (import:65, isolated_plugins:80-84, append:95). (dep:50-96,101-102; backup registry read.)
- Isolated reconciler: File + class absent (no reconcilers/isolated_plugin_venv.py; no "isolated_plugin_venv" or IsolatedPluginVenv in code except reports). TorchVenvDetector is passive detect/warn only (special-cased in dep_reconciler.py). (dep:12-13,28-47,116-146.)
- Fingerprint: Absent in all compute_hash/manifests (backend_venv: only reqs + extra gpu/numpy; plugin_bundle similar; grep 0 for policy_fingerprint in dep/). Intended only in the missing Isolated (lazy inside compute_hash: from backend.services import hardware_policy; fp = policy_fingerprint(_load_hardware or equiv)). (dep:15,224-248, specialist:177.)
- State/plugin: plugin_state.json drives enabled (audio/video disabled → dormant); data/dep_reconciler/state-*.json has shared/legacy but no isolated. User_enabled single source (via PluginStateStore). (dep:194-273,261-267.)
- Tests: Registry/classify/torch_detector/lazy/e2e etc. present (77 fns); no isolated 4-test file. (dep:173-187; tests-analyst:158.)

**Tests/verify (reduced + missing) — tests-verification-analyst + reads:**
- Counts: Policy 24 (vs 26; missing 2 VULKAN); detector 9 (match); isolated 0 (4 missing); dep 77 fns (vs 91 claimed; same in backup extraction). (tests:39-41,158-159; grep counts confirmed.)
- Content drift: No VULKAN tests; relaxed model_tier; 24gb/no_gpu tests missing KV/VULKAN asserts from backup. Isolated test absent (would import missing module + exercise fp sensitivity). (tests:43-51,95-110,153-156.)
- 33 pass sim on reduced (aligned to current impl); full handoff tests would fail (VULKAN KeyError, import, CLI, verify not found). (tests:140-166.)
- Integration: run_tests.py full pytest (picks up hardware pair but not missing); no CI pytest step; quality/preflight/release/static only (no hardware/verify gate). start.sh/dep no full policy/verify. (tests:197-215,228-232.)
- Verify script: Missing (no test harness either). (tests:113-115,176-193.)
- Positive: Existing 24+9 + dep focused, good mocks, cover degradation; detector cap tested; policy pure. (tests:234-235.)

**Open items (tmpfs, interrupt safety, AMD, plugins disabled, CLAUDE.md) — handoff + all reports:**
- TMPDIR/piptmp: None in install_pytorch.sh, setup_venvs, start, reconcilers. (provisioning:198,246-247; handoff:131-133 video ENOSPC.)
- Interrupt: No comments/traps/"DO NOT Ctrl-C mid-uninstall" (install does pip uninstall torch first). (provisioning:248; handoff:134-135 root cause of mid-session breakage caught by missing verify.)
- AMD/legacy: Policy/detector ok (but unverified AMD vram); install TORCH_CHANNEL override skipped outside NVIDIA branch / when no cap; start.sh nvidia-smi centric (misses AMD for ollama). (specialist:22-27,136-137; provisioning:249; handoff:136-140.)
- Plugins disabled: audio_foundry/video_editor user_enabled:false in plugin_state.json (reconcilers dormant; state has quarantined/failure counts). (handoff:128-130; dep:212,261; data read.)
- CLAUDE.md: Stale (path `/home/llamax1/LLAMAX8` + "git repo" per handoff note + prompt; last 2026-06-02). Design docs (handoff:147-150) absent. (handoff:141; CLAUDE read:1-16; specialist:181.)
- Other: No data/gpu_stack_status consumption in health/UI; lazy imports discipline strong (new code must obey); "Not committed" in handoff repo note (here git present but partial/dirty).

---

## 3. Git / Branch Context

- **Current:** HEAD → refs/heads/main (read .git/HEAD). Dirty working tree (user initial: untracked hardware_policy.py, test_hardware_policy.py, audio setup_venv.sh + some tests/docs; modified: start.sh, install_pytorch.sh, hardware_detector.py, many plugin jsons/start.sh, frontend pages, services (incl gpu_resource etc.), docker-compose, INSTALL.md, ollama dropin, etc.). Confirmed via reads/greps: start.sh modified (hardcodes + inline + bare calls vs backup), detector (has cap), dropin (comments), install (honors but callers don't pass), plugin starts (video inline), etc. (No root-level py untracked visible now — perhaps transient during specialist work or initial description; main partials live in backend/services/ + tests/.)
- **How partial refactor landed:** The "big-fing-refactor" (backup dated 2026-06-14) appears to have been ported incompletely into main amid ongoing "team:" / agentic work (prompt: agentic termination budget, redis reconnect, voice streaming, bare excepts sweeps, edge graceful, RAG, music-video, infra schedule fixes, etc.). .git/logs/HEAD shows recent agent/dev commits (e.g. "refactor(plugins): GPU-conflict...", "feat(websites+jobs)...", "refactor(rag):...", "chore(rag-embedding-refactor)...", merges from rag-embedding-refactor branch). 24 ahead / 9 behind origin/main indicates active divergence. Partial policy/detector/classify/audio landed (perhaps via manual port or earlier commit), but wiring (start.sh policy calls, registry registration, isolated file+test, verify, video script, VULKAN/model_tier) + test parity did not — or regressed post-landing. Dirty tree mixes refactor remnants with unrelated team changes (frontend, services, infra, etc.).
- **Implications for merge to origin/main:** High. Incomplete feature (support code without activation) + dirty uncommitted/unstaged changes (including potential policy drift files) risks polluting origin with re-buggy state + noise. Hard to bisect "team" commits from refactor. "Risk of carrying incomplete feature + unrelated changes" (prompt). Recommend clean branch + selective port from backup snapshot (exact canonical) rather than current dirty main.

---

## 4. Risks if We Merge/Push Main Now

- **Re-introduce original CPU torch / NUM_PARALLEL bugs (handoff root cause + provisioning:229-242, specialist:20):** Plugins (audio/video) get wrong torch (chatterbox pin or bare reqs; no policy override in main paths or auto setup_venv); 16GB card gets NUM=2 (or dropin 1 but inconsistent); re-creates "slow, all CPU" + 500s (libnvshmem/cupti) + offload. Backend also misses policy channel in start + reconciler paths.
- **No hw-aware rebuilds for plugins (dep:248-259, specialist:119):** GPU swap / box restore / compute_cap change (stale hardware.json) → no policy_fingerprint drift → wrong torch transitive in isolated venvs (audio_foundry/venv + venv-music, lora/venv-torch). Manual setup_venv only; detector only warns (no repair/hash/state).
- **No end-of-boot visibility (tests:195, provisioning:172,239):** Missing verify + gpu_stack_status.json means post-provisioning degradations (e.g. interrupted install_pytorch leaving torch-less venv per handoff:74-76) go undetected. Health layer / operators blind.
- **Inconsistent state across start/reconciler/plugin paths (all reports):** Detector vs policy (start uses detector); inline model dupe vs fn (future divergence); static dropin vs rendered; audio script present but dormant; backend_venv vs isolated (fp only in design); start.sh vs dep_reconciler limited --only. "Drift between shell start.sh paths, dep_reconciler, and plugin start.shs" (provisioning:282).
- **Test confidence lower than handoff (tests:174,249,309; "don't merge without full test suite"):** 33 green on pared subset (no VULKAN/isolated/verify coverage) gives false confidence. Regressions in torch channel, ollama tuning, hw-drift, plugin isolation invisible to tests/CI (no pytest in .github/ci.yml; no hardware gates in quality/preflight). Cannot re-run handoff verification cmds successfully (missing files + CLI fails + import errors).
- **Other:** Re-introduces silent Vulkan risk (no policy force); AMD/legacy latent unaddressed; tmpfs ENOSPC + interrupt torch-less risk persist (handoff opens); plugins disabled (dormant auto-provisioning); CLAUDE.md stale misleads future agents; carrying incomplete + unrelated (24 ahead) amplifies origin/main pollution + merge conflicts later. "High risk of re-introducing original bugs" (specialist:28).

**Overall:** The refactor that "fixed" the symptoms on the box is not active in current tree. Merging now ships the pre-fix disease with extra cruft.

---

## 5. Concrete Pre-Merge / Regroup Plan (Prioritized Checklist)

**Priority: HIGH (core of "every env" fix + prevent re-bugs). Do on a clean feature branch (e.g. `git checkout -b regroup/hardware-policy-wiring-2026-06-14`). Use backup snapshot as canonical source for restores/ports (exact match to verified handoff). Re-test on this box or sim after each phase. Clean/stage tree separately (e.g. `git add -p` or commit refactor as unit before unrelated). Update CLAUDE.md + add note on design (even if docs/ not present).**

**Phase 0: Prep (branch + tree hygiene)**
- `git checkout -b regroup/hardware-policy-full-wiring-2026-06-14`
- Snapshot current dirty: `git status --porcelain > /tmp/pre-regroup-dirty.txt`
- (Optional) Stash or selectively commit unrelated team work if separable; focus refactor changes.
- Confirm backup intact: `ls backups/big-fing-refactor__06-14-2026___20260614_205731/scripts/verify_gpu_stack.sh` etc.
- Read CLAUDE.md + handoff note; plan to refresh at end.

**Phase 1: Restore policy heart + tests (VULKAN + model_tier CLI + parity) — specialist:141-144, tests:269-276**
- Restore from backup (or edit current):
  - Add VULKAN to ollama_tuning() in /home/llamax1/LLAMAX8/backend/services/hardware_policy.py (nvidia=0, amd=1, degrade=0; see backup:69,81-88; update ollama_env print path).
  - Add model_tier case to main() (backup:176-179: parse ram/arch, print chat<tab>embed; update docstring:156).
  - Update tests: /home/llamax1/LLAMAX8/backend/tests/test_hardware_policy.py — add/restore 2 VULKAN tests + strengthen 24gb/no_gpu asserts + change model_tier_standard assert to exact == "llama3.1:8b" (backup test:109-113).
- Verify: `python -m backend.services.hardware_policy torch_channel` (cu128), `ollama_env` (includes VULKAN=0), `model_tier` (llama3.1:8b<tab>nomic), `fingerprint`.
- `python -m pytest backend/tests/test_hardware_policy.py backend/tests/test_hardware_detector.py -q` (expect 26+9 green post-restore).

**Phase 2: Wire start.sh + related boot (from backup) — provisioning:286-289, specialist:146-152**
- Edit /home/llamax1/LLAMAX8/start.sh:
  - Backend torch (near 848): wrap with `GUAARDVARK_TORCH_CHANNEL="$("$VENV_DIR/bin/python" -m backend.services.hardware_policy torch_channel 2>/dev/null || true)" bash ...` (backup:851-853; add comment "Make hardware_policy the authority...").
  - Ollama tuning (near 1239): derive from policy ollama_env parse (backup:1247-1251 `_POLICY_NP=... | sed ...`); export NUM from it (fallback 1); keep nvidia-smi for KV/flash.
  - Dropin (near 1263): Render to data/ollama-dropin.rendered.conf via `python -m ... ollama_env`, fallback to template if no lines, set OLLAMA_DROPIN_SRC to rendered (backup:1284-1295); update comments/header.
  - Model bootstrap (near 1359): `_MODEL_TIER=... model_tier`; if present use cut -f1/f2 (backup:1414-1418); else fallback inline.
  - End-of-boot (before "Log Files:" ~2156): Add `if [ -f "$SCRIPT_DIR/scripts/verify_gpu_stack.sh" ]; then bash ... || true; fi` (backup:2168-2170).
- Update ollama dropin header per backup (references start.sh render + MAX_LOADED).
- Ensure exports (GUAARDVARK_HARDWARE_JSON etc.) consistent.
- Dry: `bash -n start.sh` or `./start.sh --fast --test` (or with FAST_START=1); check logs for policy-derived values.

**Phase 3: Add missing video + strengthen audio setup_venv — provisioning:289, specialist:164**
- Copy/restore /home/llamax1/LLAMAX8/plugins/video_editor/scripts/setup_venv.sh from backup (57 lines: FATAL guards on PLUGIN_DIR/REPO_ROOT, policy query, pip reqs*, install_pytorch --venv override, verify_venv simple torch.cuda, DEGRADED + exit 1 on fail; set -u).
- (Optional) Update current audio_foundry/scripts/setup_venv.sh to backup strength (FATAL guards + separate build_music_venv with per-step `|| { log ...; return 1; }`).
- Ensure both executable: `chmod +x plugins/*/scripts/setup_venv.sh`.
- Note: plugin start.shs still have inline ensure_venv (may de-dupe later per provisioning rec 299).

**Phase 4: Port isolated reconciler + update registry + tests — dep:289-340, specialist:153-157, tests:278-281**
- Create /home/llamax1/LLAMAX8/scripts/dep_reconciler/reconcilers/isolated_plugin_venv.py from backup (or port; see full read:1-70): class IsolatedPluginVenv(Reconciler); id="isolated_plugin_venv:...", manifests (setup+reqs*), is_active (setup file), _hardware lazy, compute_hash (hashes manifests + policy_fingerprint(_hardware())), install (subprocess bash setup_venv; log WARN + return 0 even on rc!=0 for non-block).
  - Obey lazy: imports (hardware_policy, util hash, subprocess, base) **inside methods** (no top-level non-stdlib).
  - Update reconcilers/__init__.py if needed (minimal now).
- Edit /home/llamax1/LLAMAX8/scripts/dep_reconciler/registry.py: add lazy import `from scripts.dep_reconciler.reconcilers.isolated_plugin_venv import IsolatedPluginVenv`; after shared_plugins calc: `isolated_plugins = [pid for pid in enabled if ... classify=="isolated"]`; return list append `*[IsolatedPluginVenv(repo_root, pid) for pid in isolated_plugins]`; update docstring (backup:51,80-96).
- Create /home/llamax1/LLAMAX8/backend/tests/test_isolated_plugin_venv_reconciler.py from backup (or port; 4 tests: is_active true/false, hash stable + changes on fp monkeypatch via _make_plugin fixture; uses tmp_path).
- Run: `python -m pytest backend/tests/dep_reconciler/ -k "registry or torch or lazy or isolated" --tb=line` (incl test_lazy_imports which auto-covers new .py).
- Full dep: `python -m pytest backend/tests/dep_reconciler/ -q`.
- Manual: `python -m scripts.dep_reconciler --dry-run --repo-root .` (or with test plugin_state enabling audio_foundry).

**Phase 5: Restore verify_gpu_stack.sh + data handling — provisioning:287, tests:267,267**
- Copy/restore /home/llamax1/LLAMAX8/scripts/verify_gpu_stack.sh from backup (full 51 lines read; make `chmod +x`).
- Ensure data/ dir writable; on run it mkdir -p data/ + writes gpu_stack_status.json.
- (Later) Consume in health/UI layers (e.g. /api/health or frontend status).

**Phase 6: Address handoff open items (handoff:124-142, provisioning:293-294)**
- TMPDIR: In install_pytorch.sh + both setup_venv.sh + start.sh (before big pip): `mkdir -p "$REPO_ROOT/data/piptmp"; export TMPDIR="$REPO_ROOT/data/piptmp"` (or equiv; comment "avoid /tmp 8GB tmpfs ENOSPC").
- Interrupt safety: Add prominent comments + trap in install_pytorch.sh (and callers): "DO NOT Ctrl-C mid-flight: uninstall torch first; interrupted run leaves venv torch-less. Let finish or re-run fully."
- AMD: Audit/fix install_pytorch AMD branch to honor GUAARDVARK_TORCH_CHANNEL or policy rocm equiv; make start.sh ollama path policy-driven (not nvidia-smi only); enhance detector AMD tests.
- Plugins: Document (or temporarily flip in data/plugin_state.json for test: audio_foundry/video_editor true) that enabling via UI will now auto-track via isolated reconciler + policy fp.
- CLAUDE.md: Refresh with current path/git status + note "big-fing-refactor partial port in progress; see regroup-reports/ + backups/SESSION_HANDOFF_06-14-2026.md + REGROUP_BEFORE_MERGE... for status. Design intent: one hardware_policy → every env (start.sh + dep + verify)."
- (Optional) Add design note or copy handoff spec refs if local-workspace-only/docs exist.
- Update dropin/start comments for consistency (VULKAN via policy).

**Phase 7: Full verification + re-test (handoff cmds + more; tests:301-305, specialist:167)**
- Exact handoff (adjust for venv/python here; use system python + PYTHONPATH or backend/venv if healthy):
  ```
  python -m pytest backend/tests/test_hardware_policy.py backend/tests/test_hardware_detector.py backend/tests/test_isolated_plugin_venv_reconciler.py -q
  python -m pytest backend/tests/dep_reconciler/ -q
  bash scripts/verify_gpu_stack.sh   # expect exit 0 + data/gpu_stack_status.json + "healthy" or list
  ```
- Policy CLIs: `python -m backend.services.hardware_policy torch_channel` (→cu128 or equiv), `ollama_env` (6+ lines incl VULKAN=0 + NUM from vram), `model_tier` (8b tab nomic), `fingerprint`.
- Plugin: `bash plugins/audio_foundry/scripts/setup_venv.sh` (idempotent; expect healthy or rebuild); same for video (after script added).
- Start dry/smoke: `./start.sh --fast --no-browser --test` or equiv (check logs for "from hardware_policy", "Model tier from...", verify output, no "unknown key").
- Dep full: `python -m scripts.dep_reconciler --repo-root .` (with enabled test state if needed).
- Full suite: `python run_tests.py` (or targeted); check for lazy imports pass.
- HW sim: Manually edit ~/.guaardvark/hardware.json (swap cap/vram) or use env GUAARDVARK_HARDWARE_JSON; re-run policy/fp + (with reconciler) assert drift/rebuild trigger.
- AMD/legacy if possible (rocm-smi sim).
- Confirm no re-intros: grep -r "NUM_PARALLEL:-2" start.sh (should be gone or commented); grep for inline model dupe remnants; etc.
- `bash scripts/verify_gpu_stack.sh` post any provision.

**Phase 8: Tree clean + docs + final**
- `git status`; selectively `git add` refactor files (policy, start.sh, registry, new scripts/tests, verify, video setup, dropin, tests); separate commit(s) for "feat(hardware-policy): full wiring + verify + isolated reconciler (parity with 2026-06-14 backup + handoff)" vs other team work.
- Update CLAUDE.md (and GROK.md/AGENTS.md/WORKFLOW if present) + INSTALL.md if relevant.
- Add note in regroup-reports/ or CAPABILITIES.md re: design (one policy → every env, fp for drift safety, advisory verify).
- Re-test on box/sim post-clean.
- `git log --oneline -10`; prepare PR description tying to handoff + reports + this REGROUP doc.
- (Post-merge) Monitor first boot + enable audio_foundry/video in UI/state to exercise isolated + fp.

**Recommended commands summary (run in order after branch):**
```bash
# Prep
git checkout -b regroup/hardware-policy-full-wiring-2026-06-14
ls backups/big-fing-refactor__06-14-2026___20260614_205731/scripts/verify_gpu_stack.sh  # confirm

# Phase 1 (policy + tests)
# (edit or cp backup version for hardware_policy.py + test_hardware_policy.py)
python -m backend.services.hardware_policy model_tier  # test after edit
python -m pytest backend/tests/test_hardware_policy.py -q --tb=no

# Phase 2 (start.sh)
# (edit start.sh per backup excerpts)
bash -n start.sh
./start.sh --fast --test 2>&1 | head -100   # or dry paths

# Phase 3
cp backups/big-fing-refactor__06-14-2026___20260614_205731/plugins/video_editor/scripts/setup_venv.sh plugins/video_editor/scripts/
chmod +x plugins/video_editor/scripts/setup_venv.sh
bash plugins/video_editor/scripts/setup_venv.sh || true

# Phase 4 (dep)
cp backups/big-fing-refactor__06-14-2026___20260614_205731/scripts/dep_reconciler/reconcilers/isolated_plugin_venv.py scripts/dep_reconciler/reconcilers/
cp backups/big-fing-refactor__06-14-2026___20260614_205731/backend/tests/test_isolated_plugin_venv_reconciler.py backend/tests/
# (edit registry.py)
python -m pytest backend/tests/dep_reconciler/test_registry.py backend/tests/dep_reconciler/test_lazy_imports.py -q --tb=line
python -m pytest backend/tests/dep_reconciler/ -q

# Phase 5
cp backups/big-fing-refactor__06-14-2026___20260614_205731/scripts/verify_gpu_stack.sh scripts/
chmod +x scripts/verify_gpu_stack.sh
bash scripts/verify_gpu_stack.sh
cat data/gpu_stack_status.json

# Phase 6 (opens + CLAUDE)
# (edits for TMPDIR, comments, CLAUDE.md)
# Phase 7 (full verify cmds as above)
# Phase 8 (git add -p ; commit)
```

**Files to touch (absolute paths):**
- /home/llamax1/LLAMAX8/backend/services/hardware_policy.py
- /home/llamax1/LLAMAX8/backend/tests/test_hardware_policy.py
- /home/llamax1/LLAMAX8/start.sh
- /home/llamax1/LLAMAX8/scripts/ollama-systemd-dropin.conf (comments)
- /home/llamax1/LLAMAX8/plugins/video_editor/scripts/setup_venv.sh (new)
- /home/llamax1/LLAMAX8/plugins/audio_foundry/scripts/setup_venv.sh (optional strengthen)
- /home/llamax1/LLAMAX8/scripts/verify_gpu_stack.sh (new)
- /home/llamax1/LLAMAX8/scripts/dep_reconciler/reconcilers/isolated_plugin_venv.py (new)
- /home/llamax1/LLAMAX8/scripts/dep_reconciler/registry.py
- /home/llamax1/LLAMAX8/backend/tests/test_isolated_plugin_venv_reconciler.py (new)
- /home/llamax1/LLAMAX8/CLAUDE.md (refresh)
- /home/llamax1/LLAMAX8/scripts/install_pytorch.sh (TMPDIR + warnings; optional AMD)
- /home/llamax1/LLAMAX8/backend/tests/dep_reconciler/test_registry.py or e2e (optional extension)
- regroup-reports/ (this + perhaps archive)

**Success criteria:** Exact handoff verification cmds green; start.sh logs show "from hardware_policy" + correct NUM/tier/channel + verify "healthy"; policy CLI full; dep with enabled isolated plugin produces "isolated:xxx" state + runs setup; fp changes (cap/vram) trigger drift in test; no "unknown key"; data/gpu_stack_status.json present; TMPDIR/interrupt comments added; CLAUDE.md updated; tree staged cleanly; 0 re-intro of original bugs.

---

## 6. Next Steps / Decision Points for User

- Review this REGROUP_BEFORE_MERGE + the 4 specialist reports + SESSION_HANDOFF + backup snapshot side-by-side.
- Decide: clean branch + execute plan (recommended) vs other path (e.g. partial ship with caveats, or revert policy pieces).
- If executing: assign sub-tasks (e.g. one agent policy+tests, one start.sh wiring, one dep+registry+tests, one verify+video+opens+verify run).
- Post-regroup: re-run full tests + boot; update any PRs/commits; consider committing the feature as a unit per handoff note.
- Monitor: first real boot after enabling plugins; AMD box if available; GPU swap sim for fp.

**References (key absolute paths + reports):**
- Reports: /home/llamax1/LLAMAX8/regroup-reports/*.md (esp. hardware-policy-specialist.md:172-181 refs, provisioning:301-312, dep:341-360, tests:311-329)
- Handoff: /home/llamax1/LLAMAX8/backups/SESSION_HANDOFF_06-14-2026.md (full verified state + cmds + opens)
- Backup canonical root: /home/llamax1/LLAMAX8/backups/big-fing-refactor__06-14-2026___20260614_205731/
- Current key: /home/llamax1/LLAMAX8/backend/services/hardware_policy.py, start.sh, scripts/dep_reconciler/registry.py, data/plugin_state.json, CLAUDE.md
- Git: /home/llamax1/LLAMAX8/.git/HEAD (main); logs/HEAD (recent team/agent commits)
- This report: /home/llamax1/LLAMAX8/regroup-reports/REGROUP_BEFORE_MERGE_2026-06-14.md

**Report generated 2026-06-14.** All facts from direct tool reads (read_file, list_dir, grep) + full specialist reports + handoff. No assumptions beyond provided. Ready to tie together for merge decision.

---

*End of synthesized regroup report.*
---

## Implementation Summary (2026-06-14, post-regroup)

**Approach taken (per user concurrence + team consensus):**
- Kept the **hybrid venv model** (main backend + isolated for heavy torch plugins). This directly addresses the diagnosed root cause (chatterbox pin + ACE-Step transformer conflict + optional plugins). The dep_reconciler already had `classify_plugin_venv_mode`; we completed the "good parts".
- Ported only the high-signal useful pieces from the backup snapshot, using best judgement to avoid regressions:
  - Full canonical `hardware_policy.py` (VULKAN hardening for nvidia, `model_tier` CLI, complete docstring).
  - Full `test_hardware_policy.py` (26 tests, VULKAN cases + strict model_tier asserts).
  - `scripts/verify_gpu_stack.sh` (advisory gate + status json).
  - `plugins/video_editor/scripts/setup_venv.sh` (single-venv variant with FATAL guards + policy).
  - Upgraded `plugins/audio_foundry/scripts/setup_venv.sh` to stronger backup version (FATAL guards + explicit per-venv build functions + clear DEGRADED guidance).
  - `scripts/dep_reconciler/reconcilers/isolated_plugin_venv.py` + `backend/tests/test_isolated_plugin_venv_reconciler.py` (4 tests; `policy_fingerprint` folded into compute_hash for hw-drift safety).
  - `scripts/dep_reconciler/registry.py` updated to lazily import + register `*[IsolatedPluginVenv...]` for enabled isolated plugins (after TorchVenvDetector). classify logic was already present and now lights up.
  - Targeted wiring in `start.sh` (policy torch_channel for backend install, ollama_env derivation + prefer rendered dropin, model_tier first with inline fallback, advisory verify call at end). Preserved all existing current-tree safety (numpy re-pins, flash purges, nvidia-ml gate, limited --only reconcilers, etc.).
  - Open items addressed (best judgement, non-breaking):
    - `scripts/install_pytorch.sh`: early safe `data/piptmp` TMPDIR + PIP_CACHE_DIR (respects existing TMPDIR); prominent warning in the uninstall phase about not Ctrl-C'ing.
    - Minor header refresh on `ollama-systemd-dropin.conf` to document the new render path.
  - Left untouched: unrelated team work (agentic, RAG, music-video, frontend, etc.), current start.sh extra hardening, plugin disabled state (per design), AMD latent (documented limitation).

**Verification results (all green where expected):**
- Policy: cu128 for Blackwell 12.0, VULKAN present in ollama_tuning, model_tier + fingerprint correct.
- Tests: 39 passed (26 policy + 9 detector + 4 isolated). Dep registry/classify + torch detector tests happy.
- Shell syntax: verify, audio/video setup_venv, install_pytorch all clean.
- `bash scripts/verify_gpu_stack.sh`: runs (advisory), correctly flags video_editor as degraded in current env (old venv), writes `data/gpu_stack_status.json`.
- Registry now sees audio_foundry/video_editor as "isolated" (because setup_venv.sh files exist). Active isolated reconcilers appear when those plugins are `user_enabled:true` in plugin_state (dormant by design until enabled).
- No breakage to unrelated code paths (limited --only reconcilers in start.sh kept; existing safety logic preserved).

**Resulting state:**
- The "good parts" that made the hardware policy live and the provisioning robust are now in the tree.
- When audio_foundry or video_editor are enabled, `dep_reconciler` will use the new IsolatedPluginVenv (hw fingerprint in hash + runs the setup script).
- start.sh will derive torch channel / ollama tuning / model tier from policy and run the verify gate.
- Open follow-ups from the handoff (tmpfs, interrupt safety) have lightweight but effective mitigations.
- The multiple/isolated venv model for heavy plugins is intentionally retained (see prior analysis for rationale).

**Next for user (refactor):**
- Review the changes (`git diff`, the new files in scripts/ + plugins/video_editor/scripts/ + dep_reconciler/reconcilers/ + tests).
- Git hygiene on the dirty main (the partial refactor changes + other team work are now mixed; you may want a clean feature branch or selective commit).
- Full re-verify on your box: the handoff commands + `./start.sh` (or at least the policy CLIs + verify + enabling a plugin + dep_reconciler).
- Enable the plugins in the UI/state when ready; their venvs will now be policy-driven and tracked.

All work stayed within "pick the useful/good changes" scope. The master report above contains the full before/after context.

---

## Separate Issue (2026-06-14): Frontend chat robustness (agent mode + UnifiedProgressContext socket)

User reported:
- STRICT MODE PROTECTION reuse warn (React.StrictMode dev double-mount)
- CHAT_PATH: Blocked enhanced-chat fallback in agent mode (useUnifiedChat:true, hasService:false, connectionState:disconnected)
- UnifiedProgressContext Socket connect_error

**Fixes applied (option B - targeted robustness, one issue at a time):**

- `frontend/src/pages/ChatPage.jsx`:
  - STRICT MODE log downgraded to `console.debug` in dev (expected noise from StrictMode).
  - Service creation `useEffect` now watches `socketRef?.current` + `connectionState` (in addition to sessionId/useUnifiedChat). Recreates/joins the `UnifiedChatService` when the socket becomes available or reconnects; properly cleans up.
  - Agent-mode send path now does explicit `forceReconnect()`, short await, lazy service creation, and a retry of the unified `sendMessage` before showing the guidance error. Message updated to note that recovery was attempted. The "no-tools fallback" is still blocked for agent mode (safety preserved).
  - Reuses the existing lazy-creation pattern and `forceReconnect` call.

- `frontend/src/contexts/UnifiedProgressContext.jsx`:
  - `forceReconnect` made more aggressive: tries `.connect()` when disconnected, and also `socket.io.reconnect()` to wake the internal engine.
  - `connect_error` log now includes the active transport name for easier diagnosis of proxy / ws / polling issues.

These changes make transient disconnects (dev StrictMode, backend restart, Vite proxy hiccups, LAN proxying, etc.) far less likely to produce the exact "blocked in agent mode" state the user saw. The underlying socket still needs to be able to reach the backend (see Vite proxy for `/socket.io` + `SOCKET_URL` in apiClient.js + backend socketio_instance.py + app.py).

The previous big-fing-refactor work remains fully parked (see top of this file + active todos).

User can test by:
- Ensuring full stack via start.sh (so proxy + backend socketio are up).
- Triggering /agent, sending a message, simulating disconnect (or just watching logs on first load).
- The connect_error should be more informative, STRICT less spammy, and agent sends should auto-recover instead of immediately hard-blocking.

