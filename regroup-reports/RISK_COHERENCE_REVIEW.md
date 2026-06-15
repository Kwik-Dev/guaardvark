# Risk & Coherence Review — Agentic Regroup/Sweep (2026-06-14)

**Reviewer:** Risk & Coherence Reviewer (agentic awareness focus)  
**Goal Context:** "More architecturally and contextually aware team" for regroup/sweep/refactoring. User concern: overlooking features (from greps), better wiring in agentic aspects (personality/awareness/solidification via budget + context/memory/lessons).  
**Scope:** Full history (REGROUP report + specialists), explicit reads of agent_brain.py + supporting (brain_state.py, agent_control_service.py/ACS, agent_executor.py, unified_chat_engine.py), hardware_policy.py, recent chat changes (frontend ChatPage.jsx + UnifiedProgressContext.jsx + unified_chat_api.py + socketio_events.py), memory/entity/unified files (memory_contract.py, memory_api.py, lesson_reconciler.py, entity_indexing_service.py, unified_progress_system.py, query/enhanced paths). Broad greps + targeted reads for budget, chat_context, entity/facts/lessons, unified progress, hardware in agents, etc. MCP redis noted but out-of-scope for core analysis unless surfaced in code.

**Read Breadth Summary (key absolute paths):**
- `regroup-reports/REGROUP_BEFORE_MERGE_2026-06-14.md` (full; covers hardware wiring completion post-drift, separate frontend chat robustness fixes for agent mode/unified socket/StrictMode, agentic budget work context).
- `backend/services/agent_brain.py` (full core + _gemma4_direct, _narrate..., budget=StepBudget.from_total, charge paths, acs.execute_task(chat_context=budget_aware_context, budget=...), _instinct/_deliberate passing).
- `backend/services/brain_state.py` (StepBudget full: charge/on_escalation/to_context/to_llm_summary/from_total; TOTAL_STEP_CAP=20; get_system_prompt live {MEMORY_BLOCK}/{DESKTOP_STATE} injection via memory_api + ACS._get_desktop_state; BrainState singleton pre-compute for reflexes/tools/prompts).
- `backend/services/hardware_policy.py` (full; torch_channel/ollama_tuning (now with VULKAN)/model_tier/policy_fingerprint/_is_stale/_load; CLI; used in start.sh/dep_reconciler isolated_plugin_venv + audio/video setup_venvs).
- `backend/services/agent_control_service.py` (key sections: execute_task(..., chat_context="", budget=None, max_steps=...); effective_budget charging + remaining cap on max_iters; self._current_budget; _build_unified_prompt/_build_decision_prompt etc receive chat_context; gemma paths; _thinking_steps_buffer drain for persistence).
- `backend/services/agent_executor.py` (FactsRegistry + extract_... per-tool + format_facts_for_prompt; execute(..., session_context, budget=...); budget charge + to_llm_summary inject into session_context; facts clear/synth/verify; memory_context mix-in).
- `backend/services/unified_chat_engine.py` (CORE_TOOLS incl memory_*; TOOL_CONTEXT_KEYWORDS for agent_control/mcp/etc; select_tools; UnifiedChatEngine.chat; abort flags).
- `backend/api/unified_chat_api.py` + `backend/socketio_events.py` (AgentBrain routing if AGENT_BRAIN_ENABLED; _merge_session_mode_options for agent_screen_active; emit_fn socket room=session; recent robustness for agent).
- Recent chat fixes: `frontend/src/pages/ChatPage.jsx` (useUnifiedProgress socketRef+connectionState for service creation/recreate/join; agent-mode send with forceReconnect + retry + "recovery attempted"; STRICT downgrade to debug; process reuse guards); `frontend/src/contexts/UnifiedProgressContext.jsx` (forceReconnect aggressive .connect()+socket.io.reconnect(); connect_error with transport; unifiedJobs + reducer for job:*).
- Memory/entity/unified: `backend/services/memory_contract.py` (MEMORY_TYPES incl "lesson"/"lesson_summary"/"belief_update"; normalize/coerce; query_tokens/memory_match_score; validate_lesson_payload); `backend/api/memory_api.py` (get_memories_for_context with char_budget=max_tokens*4, _query_memories, lesson flatten to "LESSON (title): 1. ... -> ...", groups by type); `backend/services/lesson_reconciler.py` (belief_update -> group by src:file:line/element; threshold=3 -> PendingFix diffs for self_knowledge_compact.md etc.); `backend/services/entity_indexing_service.py` (index_* create entity_summary with content_type='entity_summary' + searchable_content; LlamaIndex nodes for clients/projects/etc.); `backend/utils/unified_progress_system.py` (ProcessType enum incl LLM_PROCESSING/TASK etc but no AGENT_*; create/update/error_process; socket/Redis relay); also query_api.py/enhanced_chat_api.py (entity summaries separated in context retrieval, "entity_summary {query}" boost).
- Greps covered: StepBudget/charge paths (brain_state, agent_brain, ACS, executor); injection (chat_context/session_context + to_llm_summary in ACS/executor; memory_block in brain_state); no hardware_policy calls in agent/* (251 results all provisioning/start/dep/reports); unified progress mostly jobs/indexing/generation (celery, app, tasks, jobs_api); FactsRegistry/ entity_summary in executor vs indexing/query; lesson_reconciler limited to belief_update.
- Other: `backend/services/agent_router.py` (legacy patterns for TOOL_DIRECT/AGENT_LOOP); no AGENTS.md/CLAUDE.md full in scope but referenced in REGROUP as stale.

**Analysis Structure:**
1. Risks in current budget/agent_brain changes.
2. Hardware policy integration gaps.
3. Coherence with existing patterns (injection into chat_context/entity/facts vs silos).
4. Overlooked opportunities from greps (query tools, entity summaries, memory budgets, lessons for budget events, unified progress for agent steps).
5. Risks to "agent feel" goal (personality/awareness/solidification via budget+context/memory/lessons).
6. Duplication risks + architectural smells.
7. Frontend/backend context mismatches (from recent chat fixes).
8. Risk Register (prioritized).
9. Coherence Recommendations.
10. "Contextually Aware" Checklist for Future Work.
11. Input to Phase 2 Plan.

---

## 1. Risks in Current Budget/AgentBrain Changes

**Budget as "solidification" (positive intent per brain_state.py:127-133, agent_brain:184-186 comments):** First-class StepBudget (total=20 cross-tier cap, charge(amount, tier, reason), remaining, history, to_context/to_llm_summary for LLM visibility, on_escalation). Charged explicitly in:
- AgentBrain.process (entry + tier1/2/3 + vision + escalation + gemma).
- _gemma4_direct (gemma_steps=min(remaining,12); charge entry).
- ACS.execute_task (effective_budget from passed or synthesized; charge on enter + per-iteration; cap max_iters; _current_budget).
- AgentExecutor.execute (synthesized or passed; charge enter + per-iter; inject to session_context; self._budget).

**Injected for awareness:** `budget_aware_context = ... + budget.to_llm_summary() + " (cross-tier budget visible...)"` passed as chat_context to ACS; similar in executor (budget_summary + session_context before _build_*_prompt); telemetry attaches budget state.

**Risks (scope creep, tight coupling, missing long-term ties):**
- **Scope creep in brain:** AgentBrain (originally "three-tier instinctual router" per docstring) now owns: gemma4_direct full path (self_knowledge load, history persist via models/db, screen=LocalScreenBackend, acs=get_agent_control_service(), execute_task, _narrate_agent_outcome with ollama chat + think-strip + fallback, _parse_gemma4_actions + _execute_gemma4_action full impl for click/type/hotkey/scroll/nav/screenshot/generate_image + vision fallback servo), reflex matching, deliberation heuristics, vision routing, conversational passthrough, narration extraction (NARRATION_PATTERNS/TOOL_PARAM_EXTRACTORS), telemetry, abort handling. Comments admit Phase 2 optimization intent, but current is "bloated" single entry replacing IntentClassifier/AgentRouter/UnifiedChatEngine. Violates "sits in front without modifying" (line 135).
- **Tight coupling to ACS/executor:** Direct `from backend.services.agent_control_service import get_agent_control_service` + acs.execute_task(..., chat_context=..., budget=...) in gemma path (and _instinct delegates to UnifiedChatEngine which may call tools including agent_task_execute). Executor mixes budget injection + FactsRegistry + legacy memory_manager + coordinator. ACS has internal _current_budget for prompts but prompt builders (_build_unified_prompt etc.) receive chat_context as bag (history + budget + desktop_state from separate ACS static). Breaks if budget object changes shape. No clean interface (e.g. AwarenessInjector).
- **Missing ties to memory/lessons for long-term awareness:** Budget is ephemeral (per process/session_id; cleared on new; history in-memory + JSONL telemetry only). No persistence: on charge low/0/exhaust (e.g. "budget_exhausted" reason in ACS) or escalation, no call to save_memory / memory tools / lesson_reconciler to record as "belief_update" or new lesson type. Agent cannot develop "personality-level caution" cross-session (user directive). brain_state get_system_prompt pulls live memories but budget awareness not in that flow (only explicit in agent paths). See StepBudget doc: "The agent can be made *aware* of it (via context... or even exposed as a tool / in system prompt)" — only "via context" implemented.
- **No hardware_policy feeding agent budgets (explicit user concern):** 0 calls to hardware_policy (or _load_hardware/policy_fingerprint) anywhere in backend/services/agent_* or brain_state (confirmed grep; all 251+ hits are provisioning/start.sh/dep_reconciler/setup_venvs/reports). Agent caps fixed TOTAL_STEP_CAP=20; ACS max_iterations=15; executor=10. No dynamic scale from ollama_tuning (NUM_PARALLEL/VRAM) or model_tier or torch_channel (e.g. slower CPU paths burn budget faster on weak HW). Risk: on 16GB or after GPU swap, agent "feel" (step efficiency) mismatches reality; loops timeout inconsistently. Provisioning healthy post-regroup (VULKAN/model_tier/fp wired + verify), but agent layer blind.
- **Frontend/backend context mismatches (from recent chat fixes):** Fixes targeted (ChatPage: service recreate on socketRef/connectionState + agent forceReconnect+retry+recovery note; UnifiedProgress: aggressive reconnect + transport in logs; unified_chat_api: session_mode merge forces agent_screen_active; socketio: room per session). REGROUP notes these make "transient disconnects... far less likely to produce blocked agent mode". But: budget state / thinking_steps (ACS drain) / entity facts not explicitly synced to unified progress or session history in a way frontend "UnifiedProgressContext" (used for socket in ChatPage) can surface live agent "budget remaining" or step progress uniformly. agent mode still uses ad-hoc chat:thinking + extra_data["agentThinkingSteps"] (persisted in LLMMessage); no overlap with unifiedJobs reducer or ProcessType. Risk of desync on reconnect (budget charges lost in UI "feel"; user sees partial progress). Backend socket emits vs frontend useUnifiedChat service.
- **Telemetry + abort fragility:** Telemetry logs budget but append-only JSONL (no Redis/MCP tie for live cross-agent); abort flags in unified_chat_engine (used by brain gemma/ narration) but not cross-wired to budget exhaustion (could let loop charge negative conceptually before check).

**Severity:** High for "solidification" goal. Budget visible in-loop (good for short-term), but incomplete for personality/long-term.

---

## 2. Hardware Policy Integration Gaps

Post-REGROUP: Hardware policy now "live" (full canonical in current per implementation summary: VULKAN in ollama_tuning, model_tier CLI, fp in isolated, start.sh derives NUM/tier/channel + verify + gpu_stack_status.json; audio/video setup_venvs; registry registers Isolated; tests 39 green; open items mitigated (piptmp, interrupt comments)).

**But agent layer untouched:**
- No import/use in agent_brain/brain_state/ACS/executor (or unified_chat_engine tool selection).
- BrainState model_caps / active_model from llm_service (not model_tier from policy).
- ACS vision_model default "gemma4:e4b" / servo config from servo_knowledge_store (no hw vram scaling).
- StepBudget total fixed; no hw-derived "effective remaining" (e.g. low VRAM = lower cap or higher charge cost).
- Opportunity missed: policy_fingerprint could tag telemetry/budget events for "hw drift awareness"; ollama_tuning NUM_PARALLEL could inform parallel tool budget or context size in prompts.
- Risk: Agent loops (esp vision ReACT in ACS, which is GPU-heavy) run with wrong "resource model" vs actual (e.g. after restore to different card). "Agent feel" brittle across HW (core user goal).

**Coherence note:** Policy is pure stdlib (good, pre-torch import); agents import heavy deps anyway — easy to wire via BrainState.initialize or per-process _load.

---

## 3. Coherence with Existing Patterns

**Strong existing pattern (per task directive + code):** "Always prefer injecting into chat_context/entity/facts over new silos."
- Evidence: brain_state.get_system_prompt: live DB read for {MEMORY_BLOCK} (via memory_api.get_memories_for_context) + {DESKTOP_STATE} (via ACS static, updated at request not boot); memory_contract normalizes types/sources for cross-API/tool/UI; entity_indexing produces "entity_summary" nodes (content_type metadata) consumable by LlamaIndex RAG in query_api/enhanced_chat (entity_results boosted, separated from docs); FactsRegistry in executor extracts per-observation for _synthesize/_verify (injected? via session_context facts formatting); unified_chat_engine selects tools into prompts via schemas.
- Budget work partially aligns: injected via chat_context/session_context + to_llm_summary (not new silo in BrainState fields or separate prompt builder).
- **But risks of drift:**
  - Budget introduced as dataclass + explicit param passing (brain -> gemma/tiers -> ACS/executor) + _current_budget/_budget attrs. Feels like "new first-class" in agentic core (brain/ACS/executor) vs pure injection.
  - chat_context param in ACS is overloaded bag (passed to _build_unified_prompt alongside world_state/action_history; also used for gemma direct history+budget). Not the same as brain's {MEMORY_BLOCK} substitution or executor's session_context.
  - Desktop state pulled ad-hoc in brain_state (ACS) and ACS prompts — duplication with potential staleness.
  - FactsRegistry generic phrase extraction (web_search/analyze/generic) duplicates entity summarization logic (indexing_service _create_*_summary) and memory query (memory_api). Not fed back into entity index or lessons.
  - Lessons/belief_update (memory_contract + lesson_reconciler Phase 5) only for screen contradictions (expectations from self_knowledge), not budget events or hw observations or fact contradictions from FactsRegistry.
  - No uniform "Awareness Context" injector. Each layer (brain get_system_prompt, ACS prompt builders, executor _build_*, unified_engine) reimplements memory/desktop/budget/facts pulls.
  - MCP redis (new in session): potential shared state for budgets/memory across processes/agents, but if used directly (vs via memory_contract injection), new silo risk. (Code shows voice buffers etc., but not yet agent budget.)
  - Agent "feel" breakage: Inconsistent visibility — Tier 1 reflexes have zero context injection (pure handler); gemma direct has budget+history but narration post-process strips think; vision ACS unified vs split paths differ in world_state vs chat_context; RAG entity summaries strong in chat/query paths but weak/ absent in agent_screen loops (which prioritize screen + persistent_knowledge_system from servo recipes).

**What could break agent "feel"?**
- Short-term budget awareness without long-term (lessons/memory) = agent stays "reckless" across sessions (burns 20 steps repeatedly on same task type).
- HW blind: On low-VRAM, agent vision loops feel "slow/stuck" (higher latency = more charges per "thought") but no self-correction or reduced ambition.
- Progress/visibility gaps: Long agent runs (ACS 15+ iters) invisible in unified job/progress UI (only chat:thinking spinner + buffer drain on save); user loses "context" on refresh/reconnect despite recent fixes.
- Silo facts vs entity: Agent extracts internal FactsRegistry but doesn't contribute to or query global entity summaries (RAG grounding weaker for "what do I know about Project X" during screen tasks).
- Overloaded chat_context: Future changes (e.g. more from unified progress) bloat prompts or cause prompt drift between tiers/paths.
- Reflexes + gemma bypasses: "Personality" (self-knowledge in narration) only in gemma path; Tier 1/2 lack.

**Alignment score:** 60% — budget injection good, but new dataclass/attrs + missing memory/lesson/hardward ties + non-uniform prompt paths = drift from "inject only" purity.

---

## 4. Overlooked Opportunities from Greps

**Specifics called out in task + confirmed in reads/greps:**
- **Query tools:** query_api.py has rich entity+doc+csv context retrieval (detected_entities, separate entity summaries via "entity_summary {query}" boost in enhanced_chat_api, llamaindex search). Not surfaced as dedicated tool or auto-injected in agent_brain deliberation / ACS think prompts / executor (which rely on tool_registry select or FactsRegistry). Opportunity: "query_entity" or auto pre-fetch in _needs_deliberation / unified prompt for grounding before screen actions.
- **Entity summaries:** entity_indexing_service creates rich _create_*_summary + metadata['content_type']='entity_summary' + vector nodes (clients/projects with searchable_content). Consumed in query_api (entity_nodes separated), enhanced_chat_api (boost + filter). But: ACS/executor/agent_brain paths (esp agent_task_execute / vision) don't pull entity context alongside desktop_state or chat_context. Memory tools exist ("search_memory") but entity index is RAG-specific. Overlooked for "agent feel" (screen agent could "recall" "Client Acme has email X" without user prompt).
- **Memory budgets:** memory_api.get_memories_for_context + _query uses char_budget (max_tokens*4), importance/coerce, limit=20, per-type TRUNCATE_BY_TYPE, lesson flatten. Good (aligns with StepBudget philosophy). But: no linkage — e.g. low StepBudget.remaining could reduce memory char_budget or prioritize "lesson" type; no "memory recall cost" charged against agent budget. In brain_state (live for all prompts) vs explicit in agent paths only.
- **Lessons for budget events:** lesson_reconciler + belief_update only for Phase4 screen contradictions (tags "belief_update"/"src:..." -> PendingFix for knowledge files). memory_contract supports "lesson"/"lesson_summary". No path from budget (exhaust/low/charge history in telemetry) or FactsRegistry verification failures to emit/save a lesson (e.g. via save_memory tool or direct AgentMemory insert + reconcile). Misses "solidification": agent learns "on complex UI tasks, prefer recipes or shorter paths — budget 20 is tight".
- **Unified progress for agent steps:** unified_progress_system (ProcessType many but no AGENT_LOOP/AGENT_STEP/LLM_AGENT; create_process/update/error; Redis guaardvark:progress + socket 'job_progress'/'job:event'; used in jobs_api, celery, unified_task_executor, app init, tasks like proven_csv). ACS uses private _emit_thinking (chat:thinking + buffer for LLMMessage extra_data["agentThinkingSteps"]) + _thinking_steps_buffer drain. ChatPage/UnifiedProgressContext wires socket for general + unifiedJobs, but agent progress ad-hoc (no Process for long ACS runs, no UI progress bar on "agent iteration 3/15"). Recent fixes improved socket robustness but didn't unify agent steps. Overlooked for "contextually aware" (user directive): long agent runs should appear in same progress rails as video render / indexing.

**Other from broad analysis:** 
- Redis MCP (18 tools: create_key/get_data etc): surfaced in mcp_native_proxy / CORE_TOOLS "mcp" category, but no tie to budget (e.g. persist StepBudget per-session) or lessons. Risk/opportunity for shared awareness.
- In agent_executor: memory_context via legacy MemoryManager (not memory_contract/api); duplicates brain_state live memory.
- No "budget" or hw in entity summaries or lessons yet.

---

## 5. Risks to "Agent Feel" Goal (Personality/Awareness/Solidification)

**Core user concern:** "better wiring in agentic aspects (personality/awareness/solidification via budget + context/memory/lessons)" + "overlooking features".

**High risks of breakage:**
- **Personality erosion:** Self-knowledge loaded only in gemma_direct narration (data/agent/self_knowledge*.md + persona in system); not uniformly in Tier 2/3 prompts or ACS persistent_system (which is servo recipes/knowledge). Budget "caution" is prompt text only (no tool exposure, no lesson learning). Agent may "feel" smart per-turn but dumb across sessions (repeats budget burns).
- **Awareness incompleteness:** Budget live in some paths (to_llm_summary), memories in brain_state (for chat prompts), desktop in both, entities in RAG paths only, hw nowhere. ACS vision loops (core "agent" UX) have richest internal state (_world_state, _failure_reports, _expectation_log for lessons, progress signals) but limited external injection back to chat_context or memory. User "feels" the agent is aware on screen but not of its own limits/history/entities.
- **Solidification incomplete:** StepBudget "first-class cross-tier" good, but ephemeral. No events -> memory/lessons (e.g. on "budget_exhausted" in ACS/executor, auto save lesson "I ran out of steps on vision task X; next time use recipe or fewer iters"). Hardware policy (post-regroup solid) not in agent "resource model". Unified progress not carrying agent steps (user sees chat spinner but not canonical progress for 30s+ tasks).
- **Frontend "feel" fragility:** Despite fixes, agent mode relies on useAgentRouting + unifiedChatService + socket for progress/thinking; budget/lessons/entity not exposed in UI components (LessonPearlsFloater exists but separate). Reconnect may restore history but not live budget state or in-flight facts.
- **What breaks "feel":** Escalation from Tier2 ->3 charges budget but user sees no "I am being careful because only 5 steps left" continuity; vision agent clicks inefficiently on low HW without self-adjust; long tasks invisible in global progress; facts learned in one agent run not queryable as entity_summary next chat.

**Positive:** Recent work (budget + chat fixes + post-regroup hardware + existing memory/entity contracts) is directionally correct for awareness. Tight loop in ACS (with _expectation_log -> lesson_reconciler) is a model for other awareness (budget -> lesson).

---

## 6. Duplication Risks + Architectural Smells

- **Facts vs entities vs memory:** FactsRegistry (executor: per-tool keyphrase for web/analyze/generic + synth/verify) ~ duplicates entity_indexing summaries (rich structured per client/project) + memory_api query (keyword + importance + type groups + lessons). 3 ways to "remember facts". Smell: silo'd extraction; no shared Facts contract beyond memory_contract (which is for AgentMemory only).
- **Progress systems (pre/post unify):** Still remnants (chat:thinking buffer vs unified_progress Process + Redis). Agent steps not registered.
- **Prompt construction:** Dupe logic in brain_state._build_system_prompts (static + {MEMORY}/{DESKTOP} subs), ACS _build_*_prompt (chat_context + world_state + persistent), executor _build_* (tool_schemas + session_context + budget + honesty). Memory pull duplicated (brain_state calls api directly; executor tries MemoryManager).
- **Routing:** AgentBrain (new), AgentRouter (patterns), unified_chat_engine select_tools, gemma direct bypass, legacy intent in socketio/unified_api. Smell: multiple routers amid "replace scattered routing" goal.
- **Budget/termination:** Legacy max_steps + max_iterations + max_agent_iterations (brain_state) + TOTAL_STEP_CAP + ACS config + training_mode overrides. Plus per-tool guards + abort flags. Multiple caps.
- **Hardware remnants:** Despite regroup, start.sh still has some inline + detector calls; policy fp only in isolated (good) but not broader (e.g. agent model selection).
- **Silos vs injection:** New budget dataclass + attrs in core agent services risks becoming "agent brain state" parallel to BrainState singleton. Prefer pure injection.
- **MCP/redis:** New tools registered but if direct key access for state (vs memory_contract), duplicates durable memory.

**Smell severity:** Medium-High. "Always inject" pattern is documented in contracts but not enforced in new agentic code (budget is first-class-ish).

---

## 7. Frontend/Backend Context Mismatches from Chat Fixes

**Recent (per REGROUP "Separate Issue"):** Option B targeted robustness.
- Backend: unified_chat_api merges persisted LLMSession.mode -> agent_screen_active; abort on new msg; AgentBrain if enabled.
- Frontend: ChatPage process reuse/Strict guards (debug only), eager UnifiedChatService on socketRef (no hard connState gate), agent send: forceReconnect + await + retry unified send before error guidance.
- Progress: UnifiedProgressContext forceReconnect (connect + io.reconnect), richer connect_error (transport), unifiedJobs slice for canonical job:* (alongside activeProcesses for compat).
- Benefits: Agent mode no longer hard-blocks on transient (dev Strict, backend restart, proxy); "recovery attempted" UX.

**Remaining mismatches/risks:**
- Budget state / per-iteration charges / FactsRegistry not emitted to socket as unified progress or chat:thinking consistently enough for frontend to track "steps left" live (only via thinking label + final extra_data).
- Entity summaries / memory lessons surfaced in RAG chat but not pushed to agent session context or UI (LessonPearlsFloater is present but narrow).
- Socket room=session_id good for chat, but unified progress uses job_id / process keys; agent long-tasks lack canonical job id for cross-page visibility.
- On reconnect: history restored (via sessionStateService), but in-flight budget/ACS _action_history / _failure_reports / _expectation_log are server-side only (service singleton); refresh loses "current agent feel" mid-task (mitigated by kill/supersede but not context continuity).
- No hw or budget-derived model hints in frontend routing (USE_AGENT_ROUTING etc localStorage).

**Risk:** "Contextually aware team" undermined if agent UX (frontend) doesn't reflect backend awareness wiring (budget injected server-only).

---

## 8. Risk Register

**Priority: HIGH (agent "feel" + long-term awareness erosion)**
- R1: Budget ephemeral only (no memory/lesson tie-in) → no cross-session personality/caution solidification. (Impact: high on user goal; Likelihood: high as-is.)
- R2: Hardware policy 0% wired to agents/BrainState/budgets → inconsistent "resource feel" + wrong caps on varied HW. (Post-regroup provisioning win wasted for agentic.)
- R3: Scope creep + tight ACS/executor coupling in AgentBrain → maintenance hell, bypass of unified patterns (e.g. tool select, progress).
- R4: Unified progress incomplete for agent steps (only chat:thinking + buffer) → poor visibility/context for long ReACT/vision tasks; UI desync despite recent socket fixes.
- R5: Dupe fact extraction (FactsRegistry vs entity summaries vs memory query) + inconsistent prompt injection (chat_context vs MEMORY_BLOCK vs session_context) → context drift, overlooked grounding (entity summaries), "agent feel" of amnesia.
- R6: No budget/hardward/events -> lessons (lesson_reconciler only belief_update) + memory budgets not agent-aware → missed "overlooked integrations" from greps.
- R7: Frontend/backend: agent progress/budget/entity not in unifiedJobs or live context on reconnect → UX "blocked" or unaware despite fixes.
- R8 (med): MCP/redis new state paths create potential silos vs memory_contract injection preference.
- R9 (med): Legacy max_* caps + multiple routers duplicate budget enforcement.

**Medium:**
- Overloaded chat_context bags.
- Telemetry budget attachment but no live query path.
- Reflex/gemma bypasses for full awareness (self-knowledge only in narration).

**Low (but note):** AMD latent in policy (per REGROUP) not agent-visible; CLAUDE.md stale.

---

## 9. Coherence Recommendations

1. **Enforce injection purity:** Introduce (or extend existing) a small `context_awareness.py` or use brain_state/ memory_contract more: single `get_agent_awareness(session_id, budget=None, include_hw=False, include_entities=True, query="")` that returns formatted blocks for MEMORY/DESKTOP/BUDGET/ENTITIES/FACTS/LESSONS. All prompt builders (brain/ACS/executor/unified) call it. Deprecate ad-hoc pulls. Budget remains "injected data" not first-class attr everywhere.
2. **Wire hardware to agent awareness:** In BrainState.initialize/refresh or per-process: load policy hw; derive dynamic TOTAL_STEP_CAP or per-tier costs from vram/model_tier (e.g. low vram = charge more for vision iters); expose `hw_summary = f"GPU: {tier} VRAM-limited; prefer efficient paths."` via injector. Pass policy_fingerprint to telemetry/budget events.
3. **Budget -> long-term lessons/memory:** On key events (low remaining, exhaust, high charge count in history): from ACS/executor/brain finally, call memory tools or direct via contract: save_memory(type="lesson" or "belief_update", content=json of budget event + task, tags=["budget_event", ...]). Let lesson_reconciler (or extend) surface "efficiency lessons". Expose budget as queryable "fact" or tool ("get_my_budget_status").
4. **Unify agent progress:** Extend unified_progress_system: add ProcessType.AGENT_STEP / AGENT_TASK; from ACS _emit_thinking / executor loop: progress_system.update_process( f"agent_{task_id}_{iter}", progress=..., message=label, additional_data={"budget_remaining": ..., "reasoning":...} ). Emit canonical 'job:event'. Frontend UnifiedProgressContext + ChatPage already wired — agent runs get progress bars / history. Tie to socket room.
5. **Unify facts/entities/memory:** Make FactsRegistry feed (or query) entity index (add content_type='agent_fact'); use memory_contract for normalization in FactsRegistry. In deliberation/instinct, boost entity_summary tools or auto-inject top entities (from query_api patterns) into chat_context. Add "query_knowledge" / entity tool if missing.
6. **Memory budgets agent-aware:** In get_memories_for_context (and brain_state caller), accept/charge against StepBudget (reduce limit or char_budget when remaining low). Prioritize "lesson" type on tight budget.
7. **Reduce duplication:** Consolidate routers (deprecate AgentRouter if brain is the one); centralize prompt builders or at least budget/memory injection; make narration/lesson paths share self-knowledge loading.
8. **Recent chat coherence:** Extend fixes to carry budget snapshot + last facts + hw hint in session restore / extra_data on reconnect. Make UnifiedChatService / progress context aware of "agent budget" events.
9. **MCP/redis:** Route all state through memory_contract / entity / unified progress; use redis MCP for ephemeral (e.g. live budget per session_id) only as cache, not source of truth.
10. **Test/audit:** Add coherence tests (e.g. "budget injected in all paths", "hw summary present in agent context if enabled", "agent step emits unified progress"). Grep-enforce no direct "from hardware_policy" outside provisioning + awareness injector.

---

## 10. "Contextually Aware" Checklist for Future Work

Use before any agentic/refactor change:
- [ ] Does this inject awareness (budget/hardward/model_tier/vram summary, live entities from index, recent lessons/belief_updates, memory matches) into chat_context / MEMORY_BLOCK / facts / system prompt? (Prefer this over new BrainState fields or silos.)
- [ ] Is budget charged + to_llm_summary visible? Are events (low/exhaust) persisted to memory/lessons (not just telemetry)?
- [ ] Does hardware_policy (or derived hw_summary / dynamic cap) influence behavior or visible context? (Even advisory.)
- [ ] Does this use/extend unified progress for steps (agent tasks as ProcessType)?
- [ ] Does fact extraction / new "knowledge" go through memory_contract + feed entity summaries or lessons (vs private registry)?
- [ ] Are prompt builders (all tiers, ACS, executor, gemma, unified) using the *same* injector for context? Any overload of chat_context?
- [ ] Frontend: Is new state (budget remaining, agent progress, entities) surfaced via UnifiedProgressContext / socket events / history restore? Test reconnect/agent mode.
- [ ] Long-term "feel": Can agent learn from this (lesson emission, cross-session memory)? Does it reduce duplication with existing memory/entity/query paths?
- [ ] Coherence audit: Grep for new direct ACS/brain calls or ad-hoc pulls. No new routers/caps without deprecation.
- [ ] Overlooked features: Did I check query_api/entity summaries, memory char_budget, lesson_reconciler extension, unified progress enum?
- [ ] "Agent feel" test: Run multi-turn agent vision task + budget exhaustion + HW note (if sim) + refresh/reconnect. Does agent "remember" limits, act more efficiently next time, show unified progress, ground in entities?
- [ ] Phase 2 input: Does this advance "contextual awareness layer" + wiring (budget<->memory<->lessons<->hw<->progress<->entities)?

---

## 11. Input to Phase 2 Plan

**Prioritized (tie to REGROUP post-hardware + user agentic goals):**
- **P1 (foundational, unblock feel):** Implement AwarenessInjector (or extend brain_state.get_system_prompt + memory_api) for uniform budget + hw_summary (from policy) + top entities + active lessons. Wire into *all* agent paths (brain tiers, ACS prompts, executor, gemma). Make StepBudget events emit save_memory("lesson"|"belief_update") + telemetry. (Directly addresses R1/R2/R5/R6.)
- **P2 (visibility + reconnect):** Add ProcessType.AGENT_TASK / AGENT_STEP to unified_progress_system; emit from ACS/executor loops (with budget_remaining, iteration, reasoning). Update ChatPage/UnifiedProgress to render agent progress. Extend recent socket fixes to snapshot budget/facts on emit. (Addresses R4/R7 + chat fixes gaps.)
- **P3 (memory/lessons/hardward integration):** Extend lesson_reconciler (or new budget_reconciler) for budget events + FactsRegistry contradictions. Wire policy into BrainState (dynamic caps, model hints) + awareness blocks. Make memory recall budget-aware (charge or scale by remaining). (Addresses R1/R2/R6 + greps.)
- **P4 (reduce creep/dupe):** Refactor AgentBrain to delegate more (gemma narration/execute as separate service; unify with executor facts). Consolidate fact extraction to use entity index + memory_contract. Audit/purge legacy AgentRouter + dupe caps. Centralize prompt construction or injection calls. (Addresses R3/R5/R9.)
- **P5 (frontend + MCP coherence):** Make frontend contexts consume unified agent progress + budget snapshots. Route MCP/redis through contracts (ephemeral budget cache ok). Update docs (CLAUDE.md per REGROUP) with "context injector + awareness checklist".
- **Cross-cuts:** Full verify (multi-turn agent + reconnect + budget low + enable plugins + hw swap sim); add to run_tests / quality gates; input from this review + REGROUP hardware lessons (e.g. lazy imports, advisory verify).
- **Metrics of success:** Agent on low budget uses shorter paths or lessons; hw change affects visible context/caps; long agent run shows in unified progress UI + survives refresh with steps; entity summaries appear in vision agent context; 0 new silos; greps for "budget" show injection + persistence (not just local attrs); "feel" test passes (agent "remembers" efficiency).

**References (absolute + cross):**
- All listed in Read Breadth.
- REGROUP: "agentic termination budget, ... better wiring"; hardware post-regroup state; frontend chat fixes details (lines 359-386).
- brain_state.py:124 (StepBudget doc), 852 (get_system_prompt injection), 894 (ACS desktop).
- agent_brain.py:469 (budget_aware_context to ACS), 184 (solidification comment).
- ACS:458 (budget param doc), 601 (_current_budget), 692 (chat_context to prompts).
- executor:343 (session_context note), 414 (budget to context), 305 (FactsRegistry).
- memory_contract.py:25 (types), lesson_reconciler:4 (Phase 5).
- entity_indexing:225 (content_type), memory_api:703 (char_budget), unified_progress:30 (ProcessType).
- Greps: hardware in agents=0; unified progress in agent services=0; entity in query/enhanced.
- REGROUP specialist reports for hardware patterns (lazy, verify, fp folding) to emulate for awareness.

**Report generated 2026-06-14.** All facts from direct reads (read_file), list_dir, grep (multiple strategies: broad "memory|entity|budget", targeted "StepBudget|hardware_policy|unified_progress|entity_summary", path-limited to backend/services/ etc.), REGROUP synthesis. Thorough on "agent feel" risks. Ready for Phase 2 + team use. No code changes made (review only).

*End of Risk & Coherence Review.*
