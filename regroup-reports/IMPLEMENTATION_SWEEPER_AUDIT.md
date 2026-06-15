# Implementation Sweeper Audit Report — 2026-06-14

**Role:** Implementation Sweeper  
**Task:** Audit all concrete code changes (hardware_policy/verify/isolated + chat robustness + agent_brain/StepBudget priority work) for consistency, gaps, duplication, and alignment with discovered existing patterns.  
**Sources:** Direct `read_file` + `grep` + `list_dir` on current tree + REGROUP_BEFORE_MERGE_2026-06-14.md (incl. Implementation Summary + handoff context) + specialist reports (hardware-policy-specialist.md, provisioning-reviewer.md, dep-reconciler-expert.md, tests-verification-analyst.md) + latest greps for unified/context/memory/entity/facts/budget patterns. Cross-referenced against original patterns in dep_reconciler (lazy imports, classify, Reconciler), BrainState (dataclasses/telemetry), unified_chat_engine (context injection, token budgets, memory tools), executor/ACS legacy paths, memory/entity_indexing, gpu/harware vram budgets, start.sh wiring conventions.  
**Scope of audited changes (confirmed present + read):**  
- `backend/services/hardware_policy.py` (full, with VULKAN + model_tier CLI + fp + _is_stale + _load)  
- `backend/tests/test_hardware_policy.py` (26 tests, VULKAN + strict model_tier)  
- `backend/tests/test_hardware_detector.py` (9 tests, incl. compute_cap)  
- `scripts/verify_gpu_stack.sh` (advisory, data/gpu_stack_status.json, exit 0)  
- `plugins/audio_foundry/scripts/setup_venv.sh` (policy call + FATAL guards + dual venvs + verify)  
- `plugins/video_editor/scripts/setup_venv.sh` (policy call + FATAL + single venv + verify)  
- `scripts/dep_reconciler/reconcilers/isolated_plugin_venv.py` (Reconciler subclass + fp folding in compute_hash + lazy imports + install non-fatal)  
- `backend/tests/test_isolated_plugin_venv_reconciler.py` (4 tests: is_active, hash stable/sensitive to fp)  
- `scripts/dep_reconciler/registry.py` (classify + enabled filter + lazy import + append *Isolated... after TorchVenvDetector)  
- `start.sh` (policy wraps for torch_channel/ollama_env/model_tier + rendered dropin + verify call at end)  
- `scripts/install_pytorch.sh` (honors GUAARDVARK_TORCH_CHANNEL + early TMPDIR/piptmp + Ctrl-C warning comment)  
- `backend/services/agent_brain.py` (full refactors: StepBudget integration in process/_instinct/_deliberate/_gemma4_direct, charges on all paths, to_context/to_llm_summary injections, TOTAL_STEP_CAP=20, Gemma direct gated, non-escalation handling)  
- `backend/services/brain_state.py` (StepBudget dataclass: charge/on_escalation/to_context/to_llm_summary/to_telemetry/from_total; history; placed alongside other dataclasses like ReflexResult/TierTelemetry)  
- `backend/services/agent_executor.py` (budget extensions: execute accepts budget, legacy max_steps back-compat synthesis, capping iterations, charge on entry, multiple to_llm_summary injections in prompts/next_prompt/system_prompt + FactsRegistry/ExtractedFact for tool obs + memory_context pull)  
- `backend/services/agent_control_service.py` (budget extensions: execute_task accepts budget, capping + per-iteration charges + enforcement abort when remaining<=0, _current_budget for prompt injection in _build_vision_prompt, to_llm_summary in task prompt)  
- Frontend chat robustness: `frontend/src/pages/ChatPage.jsx` (STRICT MODE log -> debug, service useEffect depends on socketRef?.current + connectionState, agent send: forceReconnect + lazy UnifiedChatService + retry before guidance error), `frontend/src/contexts/UnifiedProgressContext.jsx` (forceReconnect aggressive: connect() + io.reconnect(), connect_error logs transport name) + related (unifiedChatService.js, hooks etc.).  

All facts derived from direct tool calls on /home/llamax1/LLAMAX8/ (no assumptions from reports alone). Current state reflects post-regroup "good parts" port + subsequent agent_brain/StepBudget + chat fixes.

---

## Executive Summary (Critical but Constructive)

**Overall:** The hardware provisioning "one policy → every env" (policy + verify + isolated reconciler + registry + setup_venvs + start.sh wiring + TMPDIR/interrupt mitigations) is now **live and consistent** with REGROUP handoff intent and dep_reconciler/BrainState patterns (lazy, classify, advisory non-blocking, fp for drift). Tests parity restored (26+9+4 + dep classify). Chat robustness targeted fixes address the reported StrictMode/agent-socket race.  

**Agent/StepBudget work (priority deep dive):** First-class `StepBudget` is a solid "solidification" of termination (cross-tier 20 cap, charge history, awareness via to_* injections in brain/executor/ACS). Enforcement in loops + prompt awareness is present on main paths. Aligns with BrainState dataclass/telemetry style and "agent should see its limits" goal.  

**However, issues remain (duplication, gaps, missed sites, consistency):**  
- **Duplication risks high in agent layer:** New `FactsRegistry`/`ExtractedFact` (tool-obs fact extraction in executor) parallels existing `entity_indexing_service`, `memory_contract`, `rag_*`, `metadata_indexing_service`, and unified_chat_engine's `save_memory`/`search_memory` + `iteration_thoughts` working-memory. Budget injection (`to_llm_summary`/`to_context` + session/chat_context prepends) parallels token budget tracking + context building in `unified_chat_engine.py` (see iteration_thoughts, token_usage, memory tools). MemoryManager pull in executor duplicates unified/memory paths. This creates maintenance fork risk (facts/budgets/context drift between "agentic" vs "unified/chat/RAG/memory" systems).  
- **Budget consistency:** StepBudget is the *only* first-class budgeted thing with charge/history/to_* methods + awareness injection. Other "budgets" (VRAM in gpu_resource_policy/hardware_policy/ollama tuning + dropins; token in claude_advisor/memory_api/rag; char in memory_api) are ad-hoc numbers or estimates with no shared class, no awareness injection, no cross-"charge" enforcement. Inconsistent naming/visibility (e.g. hardware_policy derives NUM/VRAM-derived but no "budget" object). StepBudget's 20 cap + charges align conceptually with "termination budget" mentions in REGROUP/handoff but not with VRAM/token patterns.  
- **Gaps in awareness/enforcement:** Non-escalation paths (Tier 1 reflexes, conversational passthrough, default Tier 2 instinct, Gemma direct fall-through) charge the budget but **do not inject awareness** into any LLM (correct for pure-reflex, but Tier2 paths do LLM calls without per-step to_llm in some branches). Gemma direct injects (good) but caps to min(remaining,12) + charges "tier 0". Fallbacks in _deliberate/_instinct create fresh budgets (loses cross-tier history). Legacy direct executor creates in agent_router.py, self_improvement_service.py, orchestrator_service.py, agents_api.py, agent_chat_api.py etc. **bypass StepBudget entirely** (no charge, no injection, no cap from brain). ACS/executor support legacy max_steps synthesis (good back-compat) but synthesized budgets start fresh. Tier1/2 paths + ACS Gemma path may accumulate without full parent budget. No enforcement in reflex handlers themselves.  
- **Missed call sites / wiring:** `agent_router.py` (legacy paths) + orchestrator + self_improvement direct `AgentExecutor(...)` (no budget=). Some api/ paths. No propagation from `brain.process` kwargs in all callers (e.g. socketio_events, unified_chat_api pass no max_steps). Dep reconciler limited `--only` in start.sh intentionally skips isolated (ok, per design). Hardware: setup_venvs (audio/video) do **not** set TMPDIR before their pip (rely on install_pytorch's if-not-set); video/audio start.shs still have inline ensure_venv (bypass policy per REGROUP). No consumption of `data/gpu_stack_status.json` yet (per REGROUP opens). No StepBudget unit tests (only indirect via tier_routing; brain_state.py test omits the class entirely). Isolated is_active ignores user_enabled (delegated to registry filter — correct but subtle).  
- **Alignment with patterns + backward compat:** Hardware side excellent (stdlib pure policy matches dep "lazy" + "classify" + "non-fatal" intent; fp folding in isolated.compute_hash; start render comments reference policy; back-compat fallbacks for model_tier/ollama). Agent: good back-compat (max_steps -> synthetic budget) but breaks "inherited cross-tier history" promise on legacy paths. Gemma direct awareness present but "gemma4" string match + _screen_active gate is brittle (future models?). Reflex/NARRATION patterns + FactsRegistry are new "agent instinct" layer on top of unified_chat (no direct dupe of unified engine logic, but overlap in ReACT). No breakage to existing (policy CLI, detector, start --fast, limited reconcilers all preserved).  
- **Tests/verify:** 26 policy (VULKAN + strict 8b) +9 detector +4 isolated + dep registry/classify green. verify runs (flags degraded on old venvs, writes json). But no dedicated StepBudget tests; no integration test exercising budget exhaustion across tiers/Gemma/ACS; dep e2e with enabled audio/video not asserted in start-smoke. run_tests.py / quality_gate lack hardware/agent_brain gates (per tests-analyst).  
- **Other risks (from REGROUP context):** AMD latent unaddressed (policy ok, but start/ollama still nvidia-smi heavy); plugins disabled in data/plugin_state.json (isolated dormant); CLAUDE.md potentially stale; no TMPDIR in setup_venvs; interrupt safety only comment (no trap). Facts/budget injection adds prompt bloat (multiple budget_lines per step in executor).  

**Positive alignments:** Hardware now matches "canonical" handoff state per REGROUP Implementation Summary. StepBudget is clean first-class (dataclass + methods + history) vs old "remaining_steps closure". Chat fixes are minimal/robust (no over-refactor). Lazy discipline strong in isolated/registry. Budget injected "for awareness" fulfills "solidification" goal on primary paths. Verify + non-fatal install prevent boot blockers.

**Bottom line:** Hardware piece is solid + live (no re-bug risk on torch/NUM/VULKAN/fp). Agent piece delivers priority enforcement + awareness but **introduces duplication with unified/memory/entity layers + leaves legacy bypasses + untested budget core**. High value but needs tightening to avoid fork (e.g. unify "budgets" or document agent-vs-unified split). Ready for Phase 2 clean-up; do not ship without addressing gaps or explicit "agentic-only" carve-out.

---

## 1. Specific Issues (with File + Line Evidence)

### 1.1 Duplication with Existing Context/Facts/Entity/Memory/Unified Mechanisms (Critical)
- **Facts extraction duplication:** `backend/services/agent_executor.py:80-305` (FactsRegistry + ExtractedFact + extract_facts_from_observation + _extract_*_web/website/generic + format_facts_for_prompt + _synthesize/_verify using facts; called 701, 707, 1075, 1127 etc.) duplicates global mechanisms: `backend/services/entity_indexing_service.py`, `backend/services/entity_relationship_indexer.py`, `backend/services/memory_contract.py`, `backend/api/memory_api.py:722` (char budget + facts), `backend/services/rag_*` (fact-like retrieval), unified_chat_engine memory tools (`save_memory` etc. at 141-144, 436-439). Comment at unified_chat_engine:1201 even references "FactsRegistry" (shared concern). Agentic facts are tool-obs local to ReACT loop; global ones are persistent/indexed. **Risk:** Divergent fact quality/citation/storage; double-extraction cost; maintenance (change in one ignored in other).  
- **Context injection duplication:** Budget prepends (`agent_brain.py:470,1073`; `agent_executor.py:414-416` (memory_context + budget_summary), `728,812-813` (budget_block in next/system); `agent_control_service.py:2175-2176` (budget_block + chat_context_block); ACS 2178 task prompt) + history/prev attempt + memory pull parallel unified_chat_engine's iteration_thoughts (1043-1045), token_usage (1046), session context building, chat_context param, and MemoryManager usage. Also overlaps agent_brain initial_context escalation (1069-1080). `backend/services/unified_chat_engine.py` has its own ReACT + working memory summary prepended per iteration.  
- **Memory duplication:** Executor:392-410 (MemoryManager.get_smart_context + "Previous relevant learnings" injection) + ACS _recovery_memory + lessons (298, 310, 333). Duplicates `backend/services/memory_*`, unified memory tools, servo_knowledge_store.  
- **"Budget" concept fork:** StepBudget (brain_state:123-193) vs ad-hoc VRAM (hardware_policy:61, ollama_tuning, gpu_resource_policy, dropins, CAPABILITIES.md:713-717), token (claude_advisor_api:83, memory_api, rag_experiment_agent), char (memory_api). No shared base. StepBudget alone gets to_* awareness + charge history.  
- Evidence of awareness of overlap: unified_chat_engine comment mentions FactsRegistry; agent code pulls MemoryManager explicitly ("if available" try/except).  

**Alignment failure:** Does **not** reuse existing "unified/context/memory/entity" (per task check). New parallel machinery instead of extension (e.g. no reuse of unified's token budget tracker or memory contract for steps/facts).

### 1.2 Budget Changes Inconsistency with Other Budgets (VRAM/Token/Char)
- StepBudget: first-class dataclass with charge (143-158, tracks history/tier/reason/remaining), on_escalation (160), to_context (164-176, urgency strings), to_llm_summary (178-181, "[BUDGET: N/M ...]"), to_telemetry, from_total. Enforced + visible in agent loops. TOTAL_STEP_CAP=20 hardcoded in brain:138.  
- Contrast: VRAM is detector/policy-derived numbers (hardware_policy ollama_tuning:72-77 vram_mb thresholds for NUM=1/2; gpu_resource for reclaim). Token: per-LLM usage counters (no "charge" method or LLM-visible summary injected same way). No cross-budget interaction (e.g. step cap doesn't consider VRAM headroom).  
- In agent code: budget injected **in addition to** existing (e.g. executor also injects memory + facts + guard + rules). No consistency (e.g. all budgets should have .remaining / .to_summary?).  
- Per REGROUP context: "termination budget" work predates; this "solidifies" it but ignores prior budget patterns.  

### 1.3 Missed Call Sites, Enforcement, Tests, Backward Compat
- **Missed sites for budget:** 
  - `backend/services/agent_router.py:632,690,728,745`: `AgentExecutor(...)` direct (no budget=, no max_steps in all cases; e.g. 745 hardcodes 10). Bypasses brain cap/charges/injection.  
  - `backend/services/self_improvement_service.py:314`, `orchestrator_service.py:21`, `api/agent_chat_api.py:30`, `api/agents_api.py:334`: similar direct executor creates.  
  - Callers of brain.process (socketio_events:562, unified_chat_api:193, tests): rarely/never pass max_steps= or budget. Fallback always TOTAL_STEP_CAP fresh budget.  
  - ACS/executor fallbacks (agent_control:595, executor:362): synthesize new StepBudget (loses history from parent).  
- **Gaps in awareness (non-escalation + Gemma):** 
  - Tier 1 reflexes (brain:247 charges but pure no-LLM; result returned before any injection).  
  - Conversational passthrough + default Tier2 (281,301 charge; _instinct may use UnifiedChatEngine but budget only entry charge + iters=min, no per-step to_llm in all paths; see instinct at 948-955).  
  - Gemma direct (brain:468 charges "tier 0", 470 injects to_llm into chat_context passed to acs:475; good) but gated on string "gemma4" + screen_active (201-210); fall-through on NO_SCREEN_CONTEXT (385) or failure returns None (no charge awareness propagated). gemma_steps=min(remaining,12) special cap.  
  - Deliberate fallback to instinct (1048 charge but creates new budget sometimes).  
  - Executor next_prompt (728) and system (815) inject multiple times per step; ACS vision prompt (2176) once per think. But reflex/early paths + some instinct branches miss per-LLM visibility.  
- **Tests gaps:** 
  - `backend/tests/test_brain_state.py`: imports BrainState dataclasses but **no StepBudget tests** (charge, to_*, history, from_total, exhaustion).  
  - `backend/tests/test_tier_routing.py`: exercises brain.process (many calls) but **no budget/StepBudget assertions** (no exhaustion, no to_summary in results, no cross-tier charges).  
  - No test for budget in executor/ACS direct (test_agent_executor.py uses legacy paths?). Isolated test good but no fp + hardware.json sim for drift in dep e2e. No verify_gpu_stack harness test. 39+ hardware/isolated green but agent budget core un-unit-tested.  
- **Backward compat issues:** Legacy max_steps paths work (synthesize budget) but "cross-tier inherited" (brain docstring) is lost on direct executor calls or fallbacks. AgentBrain.process accepts **kwargs for max_steps pop (188) but not all entrypoints (e.g. force_tier paths) preserve. Gemma direct uses 12 sub-cap.  
- **Hardware specific:** 
  - setup_venvs call install (which sets TMPDIR) but no pre-pip TMPDIR in audio/video scripts themselves (potential ENOSPC if pip before install override).  
  - Registry appends isolated always (for enabled); is_active only setup presence (good, but if setup removed post-classify, mismatch possible).  
  - verify always checks 4 venvs (even disabled plugins); writes json but no consumer (health/api/ui) yet.  
  - start limited --only dep call intentionally omits isolated (preserves old behavior).  
  - No "unknown key" in current main() (policy has model_tier case:176).  
- Evidence of missed: grep for "StepBudget" only in brain_state + 4 services + 2 apis/tests (no agent_router); "budget" in executor/ACS is new Step + old token/vram.  

### 1.4 Other Consistency / Pattern Alignment Issues
- Hardware: Excellent (matches dep lazy: isolated 38-43,43-48 compute_hash; registry 64-93; policy pure stdlib + _load inside methods; fp in decisions only per 108-120; _is_stale nvidia-only extensible 136; start comments "single source of truth"; non-fatal return 0 in isolated:70). Matches REGROUP "good parts". Audio/video setup have FATAL PLUGIN/REPO guards + policy query (consistent). Install honors + comments.  
- Agent: BrainState placement good (StepBudget next to ReflexResult etc.). Telemetry extended (355-356). But new NARRATION/TOOL_EXTRACTORS/DELIBERATION/NO_SCREEN/CONVERSATIONAL patterns + FactsRegistry feel "layered on" rather than extending unified_chat_engine's existing ReACT/iteration logic. Gemma direct bypasses some unified? (delegates to ACS).  
- Prompt injection: Multiple budget_lines (risk of bloat/dupe strings); injected into session_context which executor treats specially (837 "Agent:" check). In ACS mixed with world/failure/pivot blocks.  
- No shared "budget" protocol (e.g. all should implement .charge/.to_llm_summary?).  
- Git/dirty: (per REGROUP) mixes with unrelated (RAG, voice, frontend etc.); no CLAUDE refresh in this sweep.  
- Plugin state: isolated dormant until user_enabled:true (correct per dep-expert).  

---

## 2. Recommendations for Tightening

1. **Unify or explicitly scope duplication (high priority):** Decide "agentic ReACT facts/budgets/context vs unified/RAG/memory/entity". Option A: Extend unified_chat_engine + memory_contract + entity to expose step/fact primitives (reuse FactsRegistry logic, add StepBudget awareness to token tracker). Option B: Document carve-out ("agent_brain/executor/ACS is the dedicated agentic loop; unified is for chat/RAG; duplication intentional for isolation"). At minimum, factor common injection helpers (e.g. `build_budget_aware_context(budget, other_context)`) and de-dupe fact extraction (share _extract_key_phrases or use existing entity detector). Remove redundant memory pull if MemoryManager already feeds unified context.  
2. **Make budgets consistent:** Introduce (or extend) a lightweight `Budget` base or protocol. Make VRAM/token/char at least expose .remaining + .to_summary() for future injection parity. Or rename StepBudget to AgenticStepBudget and keep separate. Update hardware_policy ollama_tuning comments to reference "VRAM budget" uniformly.  
3. **Close awareness/enforcement gaps:** 
   - Pass/inject budget awareness even in Tier 2 instinct branches (e.g. always prepend to_llm in UnifiedChatEngine calls when budget present).  
   - For non-LLM paths (reflex), at least log/telemetry the charge (already partial).  
   - Make Gemma direct always inherit parent budget without fresh min-cap (or document why 12).  
   - Add `budget` param (with default None) to all direct AgentExecutor(..., budget=budget) in agent_router, self_improvement, orchestrator, apis. Update callers in brain (and socketio/unified_chat) to forward max_steps= or budget= from options.  
   - Add exhaustion check before Tier1 reflex execution (rare but for cap).  
4. **Add tests + coverage (immediate):** 
   - In `backend/tests/test_brain_state.py`: add TestStepBudget class (charge returns bool, history append, to_context urgency, to_llm, from_total, exhaustion).  
   - Extend `test_tier_routing.py` (or new test_agent_budget.py): assert charges across tiers, Gemma, to_summary presence in prompts/results, exhaustion aborts escalation/loop, back-compat max_steps, legacy direct paths synthesize. Mock budget in executor/ACS tests.  
   - Add integration: pytest exercising brain.process with low max_steps + assert stops. Dep test with enabled isolated + fp monkeypatch (already in isolated unit). Smoke verify + `data/gpu_stack_status.json` in run_tests or preflight.  
5. **Hardware robustness (per REGROUP Phase 6 opens):** Propagate TMPDIR export to audio/video setup_venvs (before any pip/venv build, not just inside install_pytorch). Add trap/stronger "DO NOT Ctrl-C" in setup_venvs + install (beyond comment). Consider making isolated is_active also respect enabled (or keep delegation). Consume gpu_stack_status in /api/health or frontend GpuStatusCard (future). Strengthen video/audio start.sh ensure_venv to call setup if present (or deprecate inline).  
6. **Other tightening:** 
   - Make Gemma gate less brittle (model caps from BrainState.model_caps + version check, not "gemma4" in lower()).  
   - Centralize budget injection (e.g. helper in brain_state that returns prefixed context; use everywhere). Reduce dupe budget_block strings.  
   - Update agent_router + other direct sites to prefer brain.process or pass budget.  
   - Add lazy import discipline note + test coverage for budget in agent files (similar to dep test_lazy_imports).  
   - In start.sh dep call, consider conditional full run or note isolated only on enable.  
   - Refresh CLAUDE.md + add regroup note (as in REGROUP plan).  
7. **General:** Prefer extending existing (unified + memory + entity) over new parallel classes for agentic bits. Keep "first-class" for StepBudget but expose via BrainState. For future, add budget exhaustion as a reflex or tool.

---

## 3. Clean-up Tasks for Phase 2 (Prioritized Checklist)

**Phase 2.1: Tests & Verification (HIGH - confidence gap)**
- Add StepBudget unit tests to `backend/tests/test_brain_state.py` (full coverage of charge/history/to_*/exhaust/from_total).
- Extend `backend/tests/test_tier_routing.py` + `test_agent_executor.py` + `test_self_improvement.py` with budget assertions, exhaustion, Gemma, cross-tier, injection visibility.
- New or extend: test for budget forwarding from brain -> executor/ACS; legacy max_steps synthesis.
- Add dep/hardware integration: test enabling audio_foundry in plugin_state + run reconciler + fp drift trigger + verify json.
- Gate in `scripts/quality_gate.py` / `preflight_check.py` / `run_tests.py`: invoke hardware policy CLIs + pytest hardware/isolated/dep + bash verify (non-blocking).
- Verify post-clean: `python -m pytest backend/tests/test_brain_state.py backend/tests/test_tier_routing.py -q -k "budget or Step or tier"`, full agent tests, `./start.sh --fast --test` (check logs for policy/verify/budget), manual low-max_steps agent task.

**Phase 2.2: Close Missed Call Sites & Gaps (HIGH - correctness)**
- Audit + update all direct executor sites: `backend/services/agent_router.py`, `self_improvement_service.py`, `orchestrator_service.py`, `api/agent_chat_api.py`, `api/agents_api.py` (and callers) to accept/forward `budget: Optional[StepBudget]` (default None) + use it for capping + pass to execute. Update create sites to `AgentExecutor(..., budget=budget)`.
- In `agent_brain.py` (process + _* paths) + callers (socketio_events.py:562, unified_chat_api.py:193): always forward budget/max_steps (add to options or explicit).
- In _instinct / Gemma / reflex paths: ensure to_llm_summary injection where LLM is called (even non-escalated Tier2).
- Add budget exhaustion guard before key non-LLM decisions if total reached.
- Update ACS/executor fallbacks to inherit (clone or share) budget object rather than fresh total.
- Gemma: use state.model_caps + "gemma" in name or similar for future-proof; always pass parent budget without special 12 unless documented.

**Phase 2.3: Reduce Duplication (MEDIUM-HIGH - long-term maintainability)**
- Refactor FactsRegistry/ExtractedFact: extract common fact utils (key phrases, confidence) to shared `backend/utils/fact_utils.py` or integrate with `entity_indexing_service` / memory_contract. Update executor + any unified references.
- Centralize context/budget injection: add helpers in brain_state.py (e.g. `def apply_budget_to_context(budget, ctx: str) -> str`, `get_budget_aware_prompt_prefix`). Use in executor/ACS/brain + consider for unified_chat_engine's iteration summary.
- Memory: remove or conditionalize the MemoryManager pull in executor:392 if unified_chat already provides via session_context (or make MemoryManager feed the StepBudget facts?).
- Budgets: either (a) add minimal .to_summary() etc. to VRAM/token trackers for parity, or (b) document separation ("StepBudget is agentic termination only; others are resource estimators").
- Review unified_chat_engine ReACT vs agent_executor/brain: share more (e.g. output parser, guard) or keep split + note in docs.

**Phase 2.4: Hardware Polish (MEDIUM - per REGROUP opens)**
- Edit `plugins/audio_foundry/scripts/setup_venv.sh` + video equiv: early `mkdir -p "$REPO_ROOT/data/piptmp"; export TMPDIR=... PIP_CACHE_DIR=...` (before any pip/venv) + comment. (Install already does; this covers direct calls.)
- Strengthen interrupt: add `trap 'echo "FATAL: interrupted; venv may be torch-less. Re-run fully." >&2; exit 1' INT TERM` (or equiv) in setup_venvs + install_pytorch (beyond comment at 300-303).
- In `start.sh`: optionally call full dep_reconciler (or with isolated) post-backend if not limited; document.
- Consume `data/gpu_stack_status.json`: wire into `backend/api/gpu_api.py` or health, and frontend `components/dashboard/GpuStatusCard.jsx` or similar (read on load or via socket).
- Optional: de-dupe inline ensure_venv in plugin start.shs by calling setup_venv.sh if present (or mark as deprecated).
- Audit AMD in start/ollama (nvidia-smi guards) vs policy (document or extend).

**Phase 2.5: Docs / Hygiene / Misc (LOW-MEDIUM)**
- Update CLAUDE.md (current path, git, "agent_brain StepBudget + hardware_policy full wiring landed; see regroup-reports/IMPLEMENTATION_SWEEPER_AUDIT.md + REGROUP...").
- Add note in regroup-reports/ (or CAPABILITIES.md) re: StepBudget design (cross-tier first-class with awareness; duplication with unified scoped to agentic loops) + hardware (one policy).
- Clean any prompt bloat: consolidate duplicate budget_line injections to single prefix per prompt.
- Run full: `python -m pytest backend/tests/ -q --tb=no` (target no new regressions), `./scripts/verify_gpu_stack.sh`, policy CLIs, dep --dry with enabled plugin sim, start dry.
- Git: selective commit (hardware as unit, agent_brain/Step as unit, chat fixes separate) vs current dirty.
- Future-proof: make TOTAL_STEP_CAP configurable (env or BrainState); expose budget as tool or in base system prompt.

**Phase 2.6: Verification Commands (run after clean-ups)**
```bash
python -m pytest backend/tests/test_brain_state.py backend/tests/test_tier_routing.py backend/tests/test_agent_executor.py backend/tests/test_isolated_plugin_venv_reconciler.py backend/tests/test_hardware_policy.py -q --tb=line
python -m pytest backend/tests/dep_reconciler/ -q
python -m backend.services.hardware_policy torch_channel && python -m backend.services.hardware_policy ollama_env && python -m backend.services.hardware_policy model_tier && python -m backend.services.hardware_policy fingerprint
bash scripts/verify_gpu_stack.sh && cat data/gpu_stack_status.json
# (with audio/video enabled in data/plugin_state.json for full isolated)
python -m scripts.dep_reconciler --repo-root . --dry-run
bash -n start.sh
# Low-step agent test (manual or via api test): send /agent task with max_steps=3; assert aborts with budget message
```
Expect: all green, budget visible in logs/prompts (grep -r to_llm or telemetry), fp changes trigger rebuild, no "unknown key", verify healthy or lists components, no new dupe facts/budgets.

---

## 4. File References & Key Snippets (Absolute Paths)

- Hardware core: `/home/llamax1/LLAMAX8/backend/services/hardware_policy.py:50-89` (ollama_tuning with VULKAN), `:176-179` (model_tier CLI), `:108-120` (policy_fingerprint), `:143-158` (charge logic no, wait that's budget), start wiring `/home/llamax1/LLAMAX8/start.sh:852-853` (torch), `:1249-1253` (ollama), `:1414-1418` (model), `:2199-2201` (verify).
- Isolated: `/home/llamax1/LLAMAX8/scripts/dep_reconciler/reconcilers/isolated_plugin_venv.py:38-48` (_hardware + compute_hash fp), `:60-70` (non-fatal install), `/home/llamax1/LLAMAX8/scripts/dep_reconciler/registry.py:78-93` (isolated_plugins list + append).
- StepBudget: `/home/llamax1/LLAMAX8/backend/services/brain_state.py:123-193` (full dataclass + methods; to_context 164, to_llm 178).
- Enforcement/injection:
  - Brain: `/home/llamax1/LLAMAX8/backend/services/agent_brain.py:187-189` (create), `:247,270,281,291,301` (charges), `:469-470` (gemma to_llm), `:1072-1073` (deliberate to_context), `:952,1061` (iters min remaining).
  - Executor: `/home/llamax1/LLAMAX8/backend/services/agent_executor.py:365-369` (capping + charge + store), `:414-416` (memory + budget prepend), `:725-728,812-815` (budget_block in prompts).
  - ACS: `/home/llamax1/LLAMAX8/backend/services/agent_control_service.py:597-614` (capping + charges + remaining<=0 abort), `:2175-2176` (budget_block in prompt), `:601` (store _current).
- Dupe areas: `/home/llamax1/LLAMAX8/backend/services/agent_executor.py:80` (FactsRegistry), `:392` (MemoryManager), unified_chat_engine memory/token (above).
- Missed: `/home/llamax1/LLAMAX8/backend/services/agent_router.py:690,745` (direct executor no budget).
- Frontend: `/home/llamax1/LLAMAX8/frontend/src/pages/ChatPage.jsx:104-107` (STRICT debug), `:213-264` (useEffect socket+connectionState), `:1665-1678` (force + lazy in agent send), UnifiedProgressContext:755-777 (forceReconnect), `:380-383` (connect_error transport).
- Tests: `/home/llamax1/LLAMAX8/backend/tests/test_hardware_policy.py:68-82` (VULKAN tests), test_isolated:28-47 (fp hash), test_brain_state.py (missing Step), test_tier_routing.py (indirect only).

**References (REGROUP + specialists for original tasks):** `/home/llamax1/LLAMAX8/regroup-reports/REGROUP_BEFORE_MERGE_2026-06-14.md:316-387` (Implementation Summary + chat fixes + parked hardware), hardware-policy-specialist:10-28 (drift list), dep:10-20 (isolated absent), tests:18-30 (missing tests), provisioning:39-100 (start wiring absent).

**End of audit.** All changes read via tools. Recommendations actionable for Phase 2. Hardware "live"; agent "delivered but leaky on duplication/gaps". Ready for user review + targeted fixes.

Report generated 2026-06-14 via direct inspection. Save path: /home/llamax1/LLAMAX8/regroup-reports/IMPLEMENTATION_SWEEPER_AUDIT.md
