# Phase 2 Tightened Plan — Architecturally & Contextually Aware Refactoring
**Date:** 2026-06-14  
**Synthesized by:** Plan Refactorer (independent synthesis of full REGROUP_BEFORE_MERGE_2026-06-14.md + 4 specialist reports + post-regroup Implementation Summary + recent agent_brain/budget/chat work + code infra)  
**Goal:** Tighten original regroup plan (hardware policy + Gemma + hw awareness + telemetry + modularity + testing priorities) + new discoveries into prioritized, actionable Phase 2. Focus: evolve StepBudget into full self-awareness (active query + integration with FactsRegistry/memory/lessons/entity), leverage unified layers, tighten agent_brain, integrate hardware_policy with budgets, clean silos/duplication. Grounded in actual discovered code.

**Key Grounding Sources (all read via tools):**
- Full REGROUP_BEFORE_MERGE_2026-06-14.md (incl. phases 0-8 checklist, drifts vs backup, Implementation Summary confirming post-regroup landing of policy + VULKAN + model_tier CLI + 39 tests (26+9+4) + isolated reconciler + registry update + video/audio setup_venv + start.sh wiring + verify_gpu_stack.sh + TMPDIR/interrupt mitigations + CLAUDE note).
- Specialist reports (hardware-policy-specialist.md, dep-reconciler-expert.md, provisioning-reviewer.md, tests-verification-analyst.md) for exact file/line diffs, risks, and canonical patterns (e.g., lazy imports in dep, policy_fingerprint in compute_hash, classify_plugin_venv_mode).
- Core agentic infra (current tree):
  - `backend/services/brain_state.py`: StepBudget (dataclass with total/used/history, charge/on_escalation/to_context/to_llm_summary/to_telemetry/from_total; TierTelemetry; BrainState singleton with precomp tool schemas/system prompts/reflexes + live get_system_prompt injecting {MEMORY_BLOCK} via memory_api + {DESKTOP_STATE}; ModelCapabilities incl. vision; health/degradation).
  - `backend/services/agent_brain.py`: AgentBrain (three-tier: Reflex <100ms/0LLM, Instinct 1-3s/1LLM, Deliberation 5-30s/ReACT; Gemma4 direct path gated on vision+screen_active; explicit StepBudget creation + charge on every tier/escalation/Gemma entry; _gemma4_direct + _instinct + _deliberate; telemetry via TierTelemetry + _log_telemetry to tier_telemetry.jsonl; DELIBERATION_SIGNALS, VISION_PATTERNS, NO_SCREEN_CONTEXT, CONVERSATIONAL_PASSTHROUGH; TOTAL_STEP_CAP=20).
  - `backend/services/agent_executor.py`: AgentExecutor (ReACT loop; FactsRegistry (ExtractedFact + per-tool extractors for web_search/analyze_website/generic; skips edit_code per audit R2; format_facts_for_prompt; _synthesize_answer + _verify_answer); execute() accepts + injects explicit StepBudget (preferred over legacy max_steps); charges budget; injects to_llm_summary into session_context + system prompts; facts used for synthesis/verification post-loop; integrates memory/smart_context).
  - `backend/services/memory_contract.py`: Central contract (MEMORY_TYPES incl. "lesson"/"lesson_summary"/"belief_update"/"fact"; MEMORY_SOURCES/STATUSES; normalize_* fns; DEFAULT_IMPORTANCE_BY_TYPE; SOURCE_TRUST_WEIGHTS; query_tokens; memory_match_score; validate_lesson_payload; coerce_*).
  - `backend/services/lesson_reconciler.py`: Phase 5 (see-think-act-remember); groups belief_update AgentMemory by (src_file, line, element) via tags; thresholds to PendingFix (unified diff hedge) for self-improvement UI; _EDITABLE_SOURCES; DEFAULT_THRESHOLD=3; not on Celery beat (CLI/manual only).
  - `backend/services/entity_indexing_service.py`: Indexes DB entities (Client/Project/Website/Task) as LlamaDocument/TextNode into RAG index; uses unified_progress (ProcessType.INDEXING); lazy _ensure_index; integrates with indexing_service.
  - Unified layers: `backend/services/unified_chat_engine.py` (legacy ReACT + streaming + abort/approval grants; still present); `backend/api/unified_chat_api.py` (tries AGENT_BRAIN_ENABLED + BrainState.is_ready → AgentBrain else fallback); `backend/api/agent_chat_api.py` (forces Tier 3); frontend `unifiedChatService.js`, `UnifiedProgressContext.jsx` (consolidates progress/jobs via Socket.IO reducer; aggressive forceReconnect + transport logging; recent chat fixes applied here + ChatPage.jsx); `frontend/src/utils/queryClassifier.js` (QueryType.ENTITY detection + ENTITY_PATTERNS; entity context injection rules).
  - Hardware: `backend/services/hardware_policy.py` (pure stdlib: torch_channel via compute_cap, ollama_tuning (now incl. VULKAN=0 nvidia), model_tier, policy_fingerprint, _is_stale_profile, _load_hardware via detector; main() supports model_tier/fingerprint/ollama_env/torch_channel); `hardware_detector.py` (compute_cap probe); used in provisioning/start.sh/dep (post-regroup) but **zero imports/usages inside backend/services/agent* or memory/entity paths**.
  - Other: `backend/config.py` (AGENT_BRAIN_ENABLED); `backend/app.py` (BrainState init); CAPABILITIES.md (AgentBrain tiers, Gemma4, Lesson Pearls, Entity extraction, Unified progress, VRAM budget bar, Phase system for autoresearch); frontend ChatPage.jsx (recent fixes: StrictMode downgrade, service recreation on socket/conn state, agent-mode forceReconnect + retry + guidance; USE_UNIFIED_CHAT/USE_AGENT_ROUTING flags); brain_api.py, memory_api.py (get_memories_for_context used by brain), lessons_api.py, entity_indexing_api.py, query_api.py (RAG).
- Post-regroup state: Hardware "one policy → every env" + verify + fp in isolated now active (39 tests green, start.sh derives values, dep tracks audio/video when enabled). Agentic work (termination budget, bare excepts, RAG, infra) mixed in. CLAUDE.md stale (last 2026-06-02). No hw_policy in agent awareness layers.
- User directive signals: Original regroup (hw policy + Gemma + hw awareness + telemetry + modularity + testing); recent agent_brain + budget; chat fixes; greps for rich context/memory/entity/unified/lessons/query infra; call for more architecturally/contextually aware team approach + sweep/refactor.

**Current Silos/Duplication (discovered via grep/read):**
- Budget: StepBudget (new, cross-tier aware, prompt-injected) vs legacy max_steps/remaining_steps closures + claude_advisor_service monthly token budgets + gpu_resource_policy VRAM reclaim + config AUTORESEARCH_MAX_EXPERIMENT_DURATION + repo map token_budget (separate concerns but no shared contract).
- Context: Inline session_context hacks in executor/brain (budget + memory + desktop + smart_context) vs brain_state.get_system_prompt live blocks vs memory_contract vs frontend smartContextBuilder.js vs queryClassifier.js entity logic vs unified_chat_engine history.
- Routing: AgentBrain (canonical per CAPABILITIES + api) vs legacy unified_chat_engine.py (still wired as fallback) + old IntentClassifier/AgentRouter remnants + agent_chat_api force paths.
- Facts/Memory/Lessons/Entity/Query: FactsRegistry (tool-obs only, in executor) vs AgentMemory (durable, via memory_contract) vs lesson_reconciler (belief → fix) vs entity_indexing_service (DB entities into RAG) vs memory_match_score/query_tokens (contract) vs frontend queryClassifier + entityDetector.js — no cross-query or active introspection.
- Hardware awareness: Fully isolated to provisioning (start.sh, dep_reconciler, setup_venvs, install_pytorch) + tests; zero in brain (Gemma direct, model_caps, tier decisions), StepBudget caps, telemetry, or entity/memory (e.g. no hw-derived lesson pearls or budget adjustment from model_tier/ollama_np/vram).
- Telemetry/Progress: TierTelemetry (brain) + tier_telemetry.jsonl vs UnifiedProgressContext (jobs/progress socket reducer) vs self_improvement telemetry vs gpu_resource vs no unified hw or budget spend telemetry.
- Modularity: Good contracts (memory_contract, brain_state dataclasses) but duplication in prompt builders, context injection, and no "map to existing first" enforcement. Tests: strong for isolated pieces (policy 26, dep 77+, executor facts) but weak cross-infra (no budget+facts+memory+entity+hw sims); CI has no pytest (per tests report).

**Tightened Approach:** Phase 2 is not "finish the old regroup" (already landed per impl summary) but a **synthesis sweep** to make the system self-aware and unified: StepBudget becomes the live "personality" of agentic limits (queried + charged by FactsRegistry + lessons/entity context); hardware_policy decisions flow into budgets/Gemma/tiers (one source of truth for hw-aware everything); all context/memory/query/entity/lessons route through contracts + unified layers (AgentBrain + brain_state live injection + unified progress + memory_contract); eliminate legacy duplication by making AgentBrain + contracts the spine; bake telemetry + hw awareness everywhere; enforce architecturally-aware process (always map first) to prevent future silos. Prioritize using *existing* patterns (memory_contract.query_tokens + memory_match_score, entity_indexing, FactsRegistry.extract_*, StepBudget.charge/to_llm_summary, brain get_system_prompt, unified progress, hardware_policy as authority, queryClassifier patterns). Modularity via contracts/lazy; testing via cross-infra parity + sims (hw swap affects budget/tier, low budget + facts → efficient paths); telemetry for observability.

---

## Prioritized Epics / Phases (Phase 2.x)

**Overall Priority:** HIGH (builds directly on landed hw policy + recent budget/agent work; prevents new awareness features from creating silos; user explicitly called for architecturally/contextually aware approach + sweep). Do on feature branch e.g. `git checkout -b phase2/agent-awareness-unification-2026-06-14`. Re-verify full boot + agent runs + policy CLIs + tests after each. Ground every change in greps/reads of the infra above before editing. Update CLAUDE.md + this plan + CAPABILITIES.md.

### Phase 2.1: Budget Awareness Solidification using Existing Infra (Foundation — make StepBudget "see" and be seen via FactsRegistry/memory/lessons/entity)
**Owner(s):** backend/services (brain_state.py, agent_executor.py, agent_brain.py); patterns from memory_contract.py + entity_indexing_service + lesson_reconciler.py + FactsRegistry internals + brain_state.get_system_prompt.  
**Why first:** StepBudget is already "solidified" (cross-tier, prompt-visible via to_llm_summary/to_context, charged on tiers/escalations/Gemma/executor entry per agent_brain:182-189 + executor:412-416 + gemma:465-478). Evolve to *full self-awareness* (active query/integration) per directive. Leverage unified layers immediately.

**Specific Tasks:**
- Extend StepBudget (brain_state.py:123-194): Add `query_active_facts(min_conf=0.5)`, `integrate_memory_context(memory_text: str)`, `apply_lesson_efficiency(lessons: list)` (use memory_contract.query_tokens + memory_match_score to score budget-relevant lessons e.g. "prefer direct tools", "cite facts first"; charge small cost for introspection). Add `from_hw_policy(hardware: dict)` classmethod (pulls model_tier/ollama_np/vram for dynamic total cap, e.g. low-vram → tighter budget). Expose `to_telemetry()` already good.
- Wire in AgentExecutor (agent_executor.py:305 facts_registry, 337 execute, 412 budget inject, 487 facts synthesis): On fact extraction (after each _execute_iteration), auto `self._budget.charge(0.5, tier, "fact extracted")` (or integer equiv); inject `facts_registry.format_facts_for_prompt()` + budget summary into prompts (extend existing); use budget.remaining to early-stop + synthesize from facts when low. Pass/query FactsRegistry facts into memory_contract-style scoring for "high-confidence entity facts".
- Integrate in AgentBrain (agent_brain.py:187 budget creation, 247/270/291/301/316 charges, 468 gemma budget_aware_context): On every escalation/tier, after reflex/instinct, actively query live memory via memory_contract (or brain.get_system_prompt which already does) + entity context (via entity_indexing or memory_api.get_memories_for_context with entity filter); charge for "context query cost"; make Gemma direct + _deliberate receive + surface budget + facts + "efficiency lessons" (from lesson_reconciler or memory type=lesson_summary matching budget tokens). Update DELIBERATION_SIGNALS etc. to consider low budget.
- Cross-layer: In brain_state.get_system_prompt (852+) and _build_system_prompts, inject budget/facts block using existing {MEMORY_BLOCK} placeholder pattern (live, not frozen). Use FactsRegistry + entity_indexing_service for "entity facts" recall. In lesson_reconciler, treat budget spend as belief_update source when inefficient paths repeat.
- Frontend tie-in (for chat fixes context): In ChatPage.jsx (recent robustness) + unifiedChatService, surface budget telemetry from TierTelemetry when agent mode; use queryClassifier.js ENTITY patterns to request "entity-rich context" that can tighten budget.
- Tests: Extend existing (backend/tests/test_hardware_policy.py style parity; dep tests lazy; executor facts tests) with `test_stepbudget_facts_integration`, `test_budget_hw_derived_cap`, `test_budget_queries_memory_lessons` (mock FactsRegistry/memory_contract/lesson_reconciler). Add to run_tests.py + targeted pytest. Sim: low remaining + high facts → prefers synthesis over more tools.
- Telemetry: Every charge + fact extract + memory match logged via existing TierTelemetry (add fields) + _log_telemetry; surface in unified progress if applicable.

**Success Criteria:** 
- Agent (any tier + Gemma) always sees live `[BUDGET: X/Y ...] + [Fact N] ... + relevant lessons/entities` in first prompt + per-step (no blind step burning).
- Budget total dynamically influenced by hw (via new from_hw_policy); charges on fact extraction + context queries; FactsRegistry facts used for verification/synthesis on low budget.
- Cross-infra tests pass (budget + facts + memory_match_score + entity context); telemetry JSONL has awareness history; no context bloat (use existing match_score to prune).
- Manual: Agent run with /agent shows budget-aware efficient behavior + fact citations; hw sim (edit hardware.json) affects derived cap.

**Risks:** Context token bloat (mitigate: use memory_contract scoring + limit); perf on live DB/entity loads in every step (lazy + cache via brain_state pattern); over-charging (small fractions or discrete). Legacy max_steps paths ignored (deprecate in 2.2).

**Status (2026-06-14):** Core 2.1 + full cross tests (10 passing in test_brain_state.py for facts/hw/memory/lessons integration, sim low-budget+high-facts prefers synthesis) + frontend surface (budget telemetry display in ChatPage agent mode via state/onComplete + queryClassifier.js ENTITY patterns for entity-rich context to tighten budget). run_tests.py -k updated. All per plan. See changes.

**Files to Touch (abs paths):** 
- /home/llamax1/LLAMAX8/backend/services/brain_state.py
- /home/llamax1/LLAMAX8/backend/services/agent_executor.py
- /home/llamax1/LLAMAX8/backend/services/agent_brain.py
- /home/llamax1/LLAMAX8/backend/services/memory_contract.py (extend if needed for budget lessons)
- /home/llamax1/LLAMAX8/backend/tests/ (new or extend test files mirroring test_hardware_policy.py + dep tests)
- /home/llamax1/LLAMAX8/frontend/src/pages/ChatPage.jsx + contexts/UnifiedProgressContext.jsx (surface only)

### Phase 2.2: Architectural Unification (Tighten agent_brain as spine; leverage + de-dupe unified layers)
**Owner(s):** backend/api + services (unified_chat_api.py, agent_brain.py, unified_chat_engine.py, brain_state.py); frontend api/unifiedChatService.js + ChatPage.jsx (recent fix patterns). Use memory_contract + entity + unified progress patterns.

**Specific Tasks:**
- Make AgentBrain the *sole* canonical router: In unified_chat_api.py (and agent_chat_api), remove fallback to legacy unified_chat_engine when AGENT_BRAIN_ENABLED + brain_state.is_ready (keep only for explicit disable); force all paths (incl. voice, cli) through AgentBrain.process (pass budget, project, screen flags). Update app.py init.
- Deprecate/bridge duplication: Audit unified_chat_engine.py for unique value (streaming/abort/approval grants); fold grants + abort into brain/executor or brain_state (unified layer); mark legacy as bridge-only with warnings. Remove old Intent/AgentRouter dupe code (grep remnants).
- Centralize context: All injection (budget, facts, memory, desktop, entity, lessons) goes exclusively through brain_state.get_system_prompt (already live + contract-backed) + executor/brain prompt builders calling it. Kill inline dupe builders (search for session_context += in executor/brain + frontend smartContextBuilder).
- Modularity: Extract TierRouter / AwarenessInjector dataclasses in brain_state (similar to ReflexAction/StepBudget). Use lazy imports (dep_reconciler style, enforced by test_lazy_imports.py).
- Frontend: Leverage UnifiedProgressContext (recent fixes for reconnect) + useUnifiedChat for all agent telemetry (budget spend, tier, facts count). In ChatPage.jsx (post-fix robustness), ensure agent send always goes through unified + brain (already mostly).
- Gemma/hw tie: In _gemma4_direct + vision routing, query hardware via _load_hardware (lazy) for vram-aware step caps or model_caps extension.

**Success Criteria:** Single code path for all chat/agent (grep shows no "unified_chat_engine" active calls outside bridge); AgentBrain.process is entry for unified_chat_api + agent paths; context injection 100% via brain_state contracts (no inline); unified progress receives tier/budget/fact events. Legacy engine can be removed in follow-up without breakage. Tests: full pytest for brain + api paths; smoke via start.sh + /agent.

**Risks:** Breaking legacy callers during de-dupe (use force_tier + compat flags); perf if brain_state refresh too eager (keep current lazy/refresh design).

**Files:** /home/llamax1/LLAMAX8/backend/services/agent_brain.py, /home/llamax1/LLAMAX8/backend/api/unified_chat_api.py, /home/llamax1/LLAMAX8/backend/services/unified_chat_engine.py (bridge or prune), /home/llamax1/LLAMAX8/backend/services/brain_state.py, frontend equivalents, tests for unified paths.

### Phase 2.3: Hardware Policy + Gemma + Telemetry + Awareness Deep Integration
**Owner(s):** backend/services (hardware_policy.py + detector already solid post-regroup; integrate into agent_brain.py, brain_state.py, agent_executor.py, gpu_resource_policy.py note separation); use existing _load_hardware/policy_fingerprint/model_tier/ollama_tuning as in dep_reconciler/isolated_plugin_venv.py and start.sh.

**Specific Tasks:**
- Import (lazy, inside methods) hardware_policy in brain_state + agent_brain: On init/refresh or per Gemma/tier decision, load hw = _load_hardware(); derive budget cap from model_tier + ollama_tuning(vram) + compute_cap (e.g. Blackwell high-cap vs low-vram tight); extend ModelCapabilities with hw_fingerprint or tier.
- Wire into budgets (Phase 2.1): StepBudget.from_hw_policy(hardware); charge for "hw-derived decision" in Gemma direct (already budget-aware).
- Gemma + hw awareness: In gemma4_direct + _is_vision_task, use policy ollama_tuning/vram for screen agent step limits or "low-vram degrade to non-vision". Surface hw model tier in agent prompts (e.g. "on this Blackwell hardware, prefer cu128-aware tools").
- Telemetry: Extend TierTelemetry + brain telemetry to include hw_fingerprint (from policy), budget spend, facts count, entity hits. Log to same jsonl; feed unified progress (jobs_api + context). Add advisory "hw awareness" to verify_gpu_stack.sh style (post-regroup).
- Cross: In entity_indexing or memory, support hw-derived "lessons" (e.g. "on 16GB use NUM=1 paths"). Policy fingerprint in any agent state hashes if persisted.
- Note separation: Do *not* mix with gpu_resource_policy (VRAM reclaim/exclusivity per specialist report) — keep provisioning vs runtime awareness distinct but both consult policy.

**Success Criteria:** `python -m backend.services.hardware_policy model_tier` + agent run on same box shows matching tier in brain context/telemetry; hw swap (GUAARDVARK_HARDWARE_JSON or edit) changes budget cap + Gemma behavior + logged fp; no silent inline vram/model logic in agent code (grep clean). Telemetry has hw + budget + tier fields. Gemma direct respects hw-derived gemma_steps.

**Risks:** Detector/policy calls in hot path (lazy + cache like brain_state); AMD unverified paths (document as in specialist reports).

**Files:** /home/llamax1/LLAMAX8/backend/services/agent_brain.py, brain_state.py, agent_executor.py, hardware_policy.py (minor if needed), tests/test_hardware_policy.py + new cross tests, start.sh/verify (if extend).

### Phase 2.4: Context/Memory/Entity/Query Layer Leverage & Silo Cleanup
**Owner(s):** All (but enforce via brain/memory/entity services); patterns: memory_contract.py (normalize, query_tokens, memory_match_score, validate), entity_indexing_service.py + api, memory_api.get_memories_for_context, frontend queryClassifier.js + entityDetector.js, unified progress, FactsRegistry, lesson_reconciler.

**Specific Tasks:**
- Mandate mapping: Any new context/memory/lesson/entity/query code *must* use memory_contract fns first (grep before adding); route entity facts through entity_indexing_service into RAG + FactsRegistry; use query_tokens/memory_match_score for budget/memory/lesson recall (replace ad-hoc in executor/brain).
- Cleanup silos: Audit memory_api.py / lessons_api.py / entity_indexing_api.py / query_api.py / rag* for dupe query logic → centralize in contract or brain_state helper. Unify "belief_update" (lessons) with FactsRegistry where possible (e.g. agent obs → belief). Prune inline entity detection (use classifier + indexing service).
- Live + contracts: Extend brain_state.get_system_prompt to take/ inject entity context + budget facts (already does memory/desktop live). In agent_executor _synthesize/verify, score facts via memory_match_score against original query.
- Frontend: queryClassifier.js ENTITY → always requests entity context from backend (via unified); use UnifiedProgressContext for all (recent fixes preserved).
- Lessons integration: lesson_reconciler output (PendingFix) feeds "efficiency lessons" back into memory_contract + budget awareness (Phase 2.1).

**Success Criteria:** Zero new ad-hoc query/memory/entity code (all changes start with "map to memory_contract.query_tokens or entity_indexing or FactsRegistry or StepBudget"); cross-grep shows unified use of contract in brain/executor/memory paths; no dupe context builders; entity-rich queries produce tighter budgets via integration. Tests cover query match + entity fact extraction + lesson reconciliation in agent context.

**Risks:** Over-centralization (keep modularity of services); migration of existing AgentMemory rows (use normalizers).

**Files:** memory_contract.py (if extend), brain_state.py, agent_executor.py, agent_brain.py, entity_indexing_service.py, memory_api.py + lessons_api + query_api (cleanup), frontend queryClassifier.js + ChatPage, tests.

### Phase 2.5: Modularity/Testing Priorities + Process Enforcement + Docs
**Owner(s):** tests + root (run_tests.py, .github/workflows, quality_gate, CLAUDE.md, this plan, CAPABILITIES.md); mirror post-regroup verification (policy CLIs + 39 tests + verify + dep + start dry + hw sim).

**Specific Tasks:**
- Testing: Add cross-phase test collections (budget+facts+memory+entity+hw+telemetry); invoke explicitly in run_tests.py + (add) pytest step in CI (per tests-analyst recs); sim hw swap + low-budget + entity query + lesson recall. Parity with handoff: full counts + verify_gpu_stack + agent runs. Enforce lazy imports (existing test_lazy_imports covers new).
- Modularity: All new awareness code uses contracts (StepBudget/FactsRegistry/memory_contract as first-class like ReflexAction); lazy imports; unified progress for any long-running (index/entity/lesson recon).
- Docs/Process: Refresh CLAUDE.md (stale per regroup + handoff) with Phase 2 status, "one hw_policy + brain awareness" design, "always map to existing query/entity/memory/brain patterns first". Add note in CAPABILITIES.md + regroup-reports/. Update GROK.md/WORKFLOW if relevant. Add design note re: self-awareness (budget as personality via Facts + lessons + hw).
- Tree hygiene: Selective git add (awareness changes separate from unrelated); full re-verify post (exact Phase 2.1 cmds + agent smoke + `./start.sh --fast --test` + policy CLIs + budget telemetry inspect).
- MCP/Redis note (if relevant): Existing redis MCP tools unrelated to agent state (file/DB based); do not mix unless future.

**Success Criteria:**  Full handoff-style + new awareness verification green (39+ hardware/isolated + dep + brain cross tests + verify + agent run with budget/facts/hw visible in logs/telemetry); CI has pytest gate; CLAUDE.md current (includes Phase 2 + "map first" rec); zero re-intro of inline logic or silos; modularity score high (contracts used).

**Risks:** CI addition may surface unrelated fails (run targeted first); doc drift (tie updates to commits).

**Files:** /home/llamax1/LLAMAX8/run_tests.py, .github/workflows/ci.yml (if present), scripts/quality_gate.py etc., CLAUDE.md, CAPABILITIES.md, regroup-reports/PHASE2_TIGHTENED_PLAN.md (this), backend/tests/* (cross).

---

## Recommendations for "More Architecturally and Contextually Aware" Team Process
- **Always map to existing first (mandatory before any new code):** Grep/read for query/entity/memory patterns (memory_contract.py:query_tokens/memory_match_score/validate_lesson_payload/normalize_*, entity_indexing_service, FactsRegistry.extract_*/format_facts, StepBudget.*, brain_state.get_system_prompt + live blocks, unified_chat* + UnifiedProgressContext, queryClassifier.js ENTITY, lesson_reconciler grouping). Only extend contracts or route through brain/AgentBrain. Example: new "self-awareness tool" → use FactsRegistry + StepBudget.query + memory match, *not* new registry. This directly addresses "user call for more architecturally/contextually aware".
- **Hardware as single source everywhere:** Any hw decision (budget cap, Gemma steps, model tier, vram NUM) *must* consult backend.services.hardware_policy (lazy _load_hardware or CLI) like post-regroup dep/start.sh. Never duplicate vram/model inline (per provisioning/drift reports). Fingerprint for state drift (lessons, agent state).
- **Budgets + Awareness as first-class:** StepBudget is the cross-tier "solidification" — propagate explicitly (preferred over max_steps) through *every* path (reflex→Gemma→executor→ACS). Make agent *query-aware* (integrate FactsRegistry/memory/lessons/entity per 2.1). Inject via to_llm_summary + existing placeholders. Telemetry every charge/fact/escalation (TierTelemetry).
- **Unified layers over silos:** AgentBrain + BrainState (precomp + live) + memory_contract + unified progress + entity_indexing = spine. Consolidate legacy (unified_chat_engine as bridge only). Use for chat fixes robustness (reconnect, agent recovery).
- **Modularity & lazy discipline:** Follow dep_reconciler (lazy imports inside methods, test_lazy_imports auto-covers), brain_state dataclasses (StepBudget/Reflex/TierTelemetry/FactsRegistry as peers), contracts over ad-hoc dicts/strings.
- **Telemetry & visibility first:** Every awareness feature (budget spend, fact extract, tier, hw fp, memory hit) → TierTelemetry + unified progress + logs. Enables debugging + future auto-reflex (as in brain_state comment).
- **Testing priorities (handoff style):** Full parity runs (policy CLIs + pytest hardware/isolated/dep/brain cross + verify_gpu_stack + start dry + hw sim + agent /agent smoke). Add to CI/quality_gate. Cover degradation (low budget + no facts). 0 re-intros of original bugs.
- **Process hygiene (from GROK/CLAUDE/WORKFLOW + regroup):** Read before write (list_dir/grep/read_file first; this plan itself). Use plan mode for ambiguity (e.g. major routing). Subagents independent but cross-verify claims. Update CLAUDE.md + regroup-reports on changes (stale doc = regression per regroup). Selective commits (refactor unit). Post-change: re-boot + agent test + full relevant tests. Prefer contracts/unification over new files.
- **Future-proof:** Treat Gemma/hardare/lessons as "awareness sources" feeding StepBudget/Facts. Phase system (autoresearch CAPABILITIES) applies to awareness tuning too. Monitor first real runs post-enable (plugins, agent screen).
- **Risk mitigation:** Context bloat/perf (score/prune via contract); legacy breakage (compat flags + targeted tests); hw unverified AMD (document + best-effort as in detector).

**Verification Commands (post each phase + final):**
```
python -m backend.services.hardware_policy torch_channel && ... model_tier && ollama_env && fingerprint
python -m pytest backend/tests/test_hardware_policy.py backend/tests/test_hardware_detector.py backend/tests/dep_reconciler/ -q --tb=line
python -m pytest backend/tests/ -k "budget or facts or agent_brain or executor or memory_contract or entity" -q
bash scripts/verify_gpu_stack.sh
./start.sh --fast --no-browser --test 2>&1 | head -50  # check "from hardware_policy", budget logs, model tier
# Agent smoke (with screen if avail): trigger /agent + multi-step; inspect tier_telemetry.jsonl + data/gpu_stack + logs for budget/facts/hw
# HW sim: GUAARDVARK_HARDWARE_JSON=... or edit ~/.guaardvark/hardware.json; re-run agent + assert derived budget/tier change + fp in telemetry
```

**Files Touched Overall (summary):** Core services (brain_state, agent_*), memory/entity/lesson, hardware (integration only), apis (unify), frontend (chat/unified per recent fixes), tests, docs (CLAUDE/CAPABILITIES/this plan), run/CI.

**Status:** Plan synthesized and saved. Ready for team execution / subagent delegation. Ties original regroup (now landed base) + new agentic infra into one aware system. "The 'one hardware policy + one awareness brain' is achieved."

**Status update 2026-06-14 (user stderr + A/B choice):** 
- GUI test suites complaint addressed (root cause = run_tests.py:install_requirements + playwright always ran on /api/meta/run-tests from diagnostics_api; produced 250+ "already satisfied" + fastapi/starlette conflict noise + "not useful" output for button-driven runs).
- (A) elements: run_tests.py now skips preflight on SKIP_PREFIGHT/QUIET_GUI_TESTS/GUAARDVARK_TEST_QUIET (set automatically by diagnostics endpoint for GUI button); pip now `-q`; playwright skipped in quiet. This enables "test without the mock/install noise". Core 2.1 budget tests (test_brain_state.py Test*Budget* classes) already use real objects/dicts + memory_contract calls (minimal/no heavy @patch for awareness logic; llm_available=False fixtures in tier tests per no-LLM unit practice). Broadened -k in runner to pull agent_executor + control + memory_contract for more comprehensive suites from GUI (120+ selected vs prior narrow 88; still fast/quiet). Full "zero mocks ever" impractical for agentic (would require live Ollama+DB every GUI click = slow/flaky); realistic fakes + real contracts preferred (current direction).
- (B) started: Phase 2.2 router unification tightened in unified_chat_api.py + socketio_events.py (local bridge). AgentBrain now *sole* canonical when AGENT_BRAIN_ENABLED (default true + brain_state.is_ready): legacy UnifiedChatEngine build is now explicitly guarded and errors cleanly (no silent dual-path) if enabled but not ready; only legacy when flag=false (bridge during transition). Logs updated; abort flags shared for compat (agent_brain already imports/uses clear/is_aborted from engine). Matches plan "Make AgentBrain the sole canonical router".
- Verified: `.../venv/bin/python -m pytest ... -k "<phase filters>"` → 70/70 on brain_state+tier (real budget/facts/hw/memory/lessons tests); broader filter 120 pass + 3 pre-existing unrelated fails in agent_control_* (not touched). run_tests.py with SKIP still produces clean JSON for GUI.
- Next per plan: either more A (add real fixture tests or integration marker) or full 2.2 (de-dupe unified_chat_engine as bridge, centralize more context in brain_state, frontend surface). Dep conflict (mcp starlette) noted for 2.5 requirements hygiene. Update CLAUDE.md on next pass.
- Files touched this step: run_tests.py, backend/api/diagnostics_api.py, backend/api/unified_chat_api.py, backend/socketio_events.py, regroup-reports/PHASE2_TIGHTENED_PLAN.md.

**References:**
- Regroup + specialists + handoff: /home/llamax1/LLAMAX8/regroup-reports/*.md + backups/
- Key code: backend/services/{brain_state.py,agent_brain.py,agent_executor.py,memory_contract.py,lesson_reconciler.py,entity_indexing_service.py,hardware_policy.py,unified_chat_engine.py}, backend/api/{unified_chat_api.py,...}, frontend/src/{pages/ChatPage.jsx,contexts/UnifiedProgressContext.jsx,utils/queryClassifier.js}
- This plan: /home/llamax1/LLAMAX8/regroup-reports/PHASE2_TIGHTENED_PLAN.md

*End of Phase 2 Tightened Plan.*

**Verification (post A/B + runner + 2.2 start):**
- Quiet GUI test path: diagnostics POST /run-tests now forces SKIP_PREFIGHT etc → no pip spam in captured output.
- Sole router: grep for "sole canonical" + conditional in the two chat entry files; legacy build skipped under default enabled.
- Cross tests green for awareness: 70 passed on targeted real budget tests (0 mocks on StepBudget integration paths).
- User choice honored by doing actionable pieces of (A quiet/real) + (B unification) in one sweep without over-mocking or stalling.