# CONTEXT & MEMORY INTEGRATOR FINDINGS
**Date:** 2026-06-14  
**Scope:** Deep-dive re-greps + code reads across backend/api, backend/services, backend/utils, frontend/src for query/entity/context/memory/lessons/unified/facts patterns + budget (StepBudget) integration opportunities.  
**Goal:** Map existing patterns for contextually grounded agents; identify how to treat budget (state, charges, remaining) as queryable entity/memory/fact/lesson; propose unified context injection + agentic solidification (avoid siloed budget code); highlight overlooked features/gaps. Exhaustive, with file/line references.

## 1. Re-performed / Deep Greps Performed
Used `grep` tool (multiple parallel calls) with patterns for:
- EntityContextEnhancer | entity_context_enhancer | entity summary | Entity summary (found 18 hits, primarily backend/utils/entity_context_enhancer.py + callers in api/generation_api.py:406, bulk_generation_api.py:394, enhanced_context_generation_api.py:271, enhanced_chat_api.py:144).
- FactsRegistry | facts_registry | ... (24 hits; core impl in backend/services/agent_executor.py:80 class + usage 305,385,487-489,514-517,596,697,707,713,935,1007,1013,1018,1090,1185).
- get_memories_for_context | get_memories | memories_for_context (32 hits; def in backend/api/memory_api.py:670 + callers in unified_chat_engine.py:2494/2499/2508, brain_state.py:865/868, agent_control_service.py:3208 comment, tests).
- apprentice_engine | ApprenticeEngine (17 hits; backend/services/apprentice_engine.py:42 class + integration in agent_control_service.py:1419,1427,1461).
- lesson | lessons | Lesson | Lessons | pearl | pearls | lesson_reconciler (87+ hits across socketio_events.py:281, migrations, memory_api.py:9,105,111,128,154,159,171,205,221,324,327,343,347,357,360,385,497,685,706,711,743,746,823,839,846,867,875,888,902, agent_control_api.py:492,756,768,773,788,803,810,820,867,877,916,920,922, lessons_api.py, lesson_reconciler.py, tasks/memory_maintenance_tasks.py:70,92).
- unified_chat_engine | UnifiedChatEngine | unified.*engine | unified_chat | unified_progress | unified.*progress (340+ hits; unified_chat_engine.py:768 class + heavy usage in socketio, apis, services/agent_brain.py:27/941/948/954, utils/unified_progress_system.py, many task/api files; unified progress as separate concern from chat context).
- StepBudget | step_budget | budget | Budget | charges | remaining.*budget | budget.*state (61+ hits concentrated in services/brain_state.py:123 class + to_context:164, to_llm_summary:178; agent_brain.py:24/182/187/215/225/230/247/270/275/281/285/291/296/301/306/316/322/352/354/355/375/464/466/469/470/475/477/923/949/952/1027/1047/1060/1072/1073/1085/1086; agent_executor.py:14/337/343/346/359/362/365/368/369/412/414/415/724/725/726/728/738/811/812/813/815; agent_control_service.py:25/454/458/595/600/601/607/614; plus scattered token/VRAM budgets in claude_advisor_service, config, ollama_resource_manager, etc. NO cross-persistence to memories/entities).
- agent_brain | AgentBrain | agent_executor | AgentExecutor | ACS | prompt builder (30+ hits; services/agent_brain.py, agent_executor.py, agent_control_service.py, api/unified_chat_api.py:102/192, agents_api.py, brain_api.py).
- chat_context | session_context | context injection | inject.*context | context.*inject | reflexes | self_knowledge | SelfKnowledge (29+ hits; widespread in agent_* files, memory_api.py:493 comment, enhanced_chat_api.py, orchestrator_service.py, brain_state.py:238 _build_default_reflexes, api/agent_control_api.py:600, tasks/self_improvement_tasks.py:116, servo_knowledge_store.py, tests/test_reflexes.py).
- Additional broad: "context|memory|entity|fact|facts|lesson|lessons|unified|query|budget" limited to api/ (many), services/ (many), utils/ (many), and frontend/src (482 hits, heavy on entities/links, session memory preservation, memory UI, unified progress context, but ZERO StepBudget/agent budget awareness).

Greps covered backend/api/*, backend/services/*, backend/utils/*, frontend/src/**/*.{js,jsx}. Frontend patterns focus on entity linking (utilService.js, entityDetector.js, usePageContext.js), memory mgmt UI (MemoryManagementSection.jsx, LessonSummaryModal.jsx, ChatPage.jsx), session context preservation (sessionStateService.js), unified progress (UnifiedProgressContext.jsx), query classification for entity injection (queryClassifier.js), but budget is absent outside Claude token % in UncleClaudeSection.jsx / DevToolsPage.jsx.

## 2. Key Files Read (Exhaustive Sections)
- **backend/api/memory_api.py**: Full read (lines 1-100 intro/imports; 480-490 recall; 493-820 critical `_query_memories` (503), `get_memories_for_context` (670-820: groups fact/note/preference/lesson_summary/other, JSON-flatten lessons at 746-808, char_budget at 703, callers note at 498/682); 823-902 `get_lessons_for_agent_prompt` (sources lesson_summary/manual + belief_update, Markdown format)). Comments at 3-16, 497-500, 682-688, 834-842 document the injection contract. Uses memory_contract.py.
- **backend/utils/entity_context_enhancer.py**: 1-100 (class init 19, enhance_query_context 26); 100-150 (patterns 92, _extract 78); 250-350+ (relationship queries for clients/projects/websites/tasks/docs with heavy safety/ null checks; _build_enhanced_context 577, _build_relationship_summary 640). Used for RAG/entity summaries.
- **backend/services/brain_state.py**: 1-100 (dataclasses, ReflexResult 67, ReflexAction 77, TierTelemetry 86); 100-150 (StepBudget full 123-193: dataclass total/used/history, charge 143, on_escalation 160, to_context 164 ("Cross-tier agentic step budget..."), to_llm_summary 178 ("[BUDGET: X/Y...]"), to_telemetry 183, from_total 192); 238+ (_build_default_reflexes); 580+ (_build_system_prompts 594: prefixes {MEMORY_BLOCK}, {DESKTOP_STATE}).
- **backend/services/agent_executor.py**: 1-100 (imports, ExtractedFact 50, AgentStep 61, AgentResult 71, FactsRegistry 80: __init__ 83, extract_facts_from_observation 88, _extract_* helpers 128+); 300-450 (execute 337: budget param doc 346, effective_budget handling 359-369, _budget store 369, session_context prepend 414-416, facts clear 386, memory mgr context 393-410); 400-500 (prompt build 441, facts synthesis 487-498); 500-650 (_execute_iteration, facts extraction 697, format 713); 680-750 (next_prompt budget_block 724-738, 812-813); 780-850 (_build_system_prompt 786: budget_line 812, base 815; _build_system_prompt_native 842; facts used in nudge 596).
- **backend/services/agent_brain.py**: 1-100 (imports StepBudget 24, process entry 158); 150-350 (budget creation 187, charges per tier/escalation 225/247/270/281/291/301/316, _gemma4_direct 211/465-478 budget_aware_context + to_llm_summary 469-470 + pass-through); 450-550 (gemma direct budget charge 468, acs call 471 with budget); 920+ (other tier entries with budget).
- **backend/services/agent_control_service.py**: 1-50 (imports StepBudget 25); 440-480 (execute_task 454: budget doc 458-464 "solidification" language, effective 592-601); 590-620 (budget enforcement loop 607-614, _current_budget 601); 680-750 (prompt calls); 1600-1670 (lesson writes 1612, distillation dispatch 1653); 2060-2200 (_build_unified_prompt 2062: budget_block 2174-2176 using to_llm_summary + chat_context, injected at 2178; other _build_*_prompt).
- **backend/services/apprentice_engine.py**: 1-100 (class 42, execute 81, autonomy levels); used for graduated replay + lessons.
- **backend/services/unified_chat_engine.py**: 768 class; 840-920 (_run_chat, memory get 2494+); 2478 (_build_system_prompt: memory_block 2492-2519 via get_memories_for_context + desktop; no budget yet); heavy tool/context selection.
- **backend/services/lesson_reconciler.py**: 1-100 (scan_belief_updates 168: buckets belief_update memories -> PendingFix for self_knowledge files); 100-242 (hedging, diffs).
- **backend/api/lessons_api.py**: 1-100 (distill 49, pearls->JSON lesson_summary memory 232 via add_memory); 100-250 (LLM distill prompt for parameterized steps 110+, fallback, save with source="lesson_summary", lesson_id).
- **backend/services/entity_indexing_service.py**: 1-50 (indexing clients/projects/etc as entity_summary docs 205+); 200-300 (index_client 205, index_project 251: metadata content_type:'entity_summary' 225/280, insert to LlamaIndex); RAG integration.
- **backend/api/entity_indexing_api.py**: 1-50 (endpoints /index-all, /index-entity).
- **backend/services/entity_relationship_indexer.py**: 1-50 (system overview doc with entity relationships + jobs).
- **backend/services/memory_contract.py**: 1-100 (MEMORY_TYPES incl lesson/lesson_summary/belief_update 25-33; SOURCES 41; TRUST_WEIGHTS; normalize fns).
- **backend/models.py**: AgentMemory 2463-2525 (fields: content, source, session_id, project_id, lesson_id 2478, type, importance, tags, extra_data, access_count etc.); AgentMemoryAudit.
- Others: enhanced_chat_api.py (entity enhancer + universal RAG entity_context), bulk_*/generation_api (entity context), brain_api.py (reflexes_count), agent_control_api.py (pearl distills, lessons), unified_progress_system.py (separate from memory context), frontend files for UI patterns (no agent budget).

Also browsed tests (test_memory_api_lessons.py, test_apprentice_engine.py, test_brain_state_memory_block.py, test_reflexes.py) confirming injection contracts.

## 3. Comprehensive Mapping of Existing Patterns
### 3.1 Memory / Context Injection (Unified, Queryable Durable State)
- **Durable Memory Tiers** (memory_api.py:3-16, memory_contract.py:3-12): Turn context (transient), CLI working, session history (LLMMessage), durable AgentMemory (fact/note/preference/lesson/belief_update), lessons (structured), belief_updates (contradictions), rules/self-knowledge (separate, higher rank).
- **Query Core**: `_query_memories` (memory_api.py:503-667): filters sources/types/status/scope (session/project/workspace/user/global), hybrid rank (importance 0.35 + trust 0.20 + match 0.25 + recency/scope), always_on high-import facts/notes, side-effect access_count/last_accessed. Used by both formatters.
- **get_memories_for_context** (memory_api.py:670-820): limit + max_tokens budget (char_budget=4*tokens 703); groups + SECTION_HEADERS (fact/note/preference); lesson_summary special JSON-> "LESSON (title): 1. ... -> ... | PARAMETERS: {p} (desc e.g. ex)" flattening (746-808, 1200 char cap); called live during prompt construction by unified_chat_engine.py:2499 (with query/session/project/cli_working_memory), brain_state.py:868 (in get_system_prompt?), agent_brain comments.
- **get_lessons_for_agent_prompt** (memory_api.py:823-902): more room for ACS/screen agent (max_chars 2500); Markdown "### Title\n  1. step"; merges belief_updates; sources lesson_summary + manual; called from ACS paths.
- **Persistence Path for Lessons**: lessons_api.py _distill_lesson_pearls (from ToolFeedback positive pearls + convo context -> LLM JSON {title,steps,parameters} -> add_memory source="lesson_summary", type="lesson", lesson_id, importance 0.85, metadata); also per-pearl in agent_control_api.py (deprecated path). End-lesson brackets for coherent learning.
- **Belief/Lesson Reconciliation**: lesson_reconciler.scan_belief_updates (belief_update AgentMemory -> group by src:file:line:element -> PendingFix for self_knowledge_*.md / recipes.json when threshold met; hedges like <!-- belief-update: N sessions... -->); memory_maintenance_tasks wraps it.
- **Entity Context / RAG Summaries** (separate but complementary to AgentMemory):
  - EntityIndexingService (entity_indexing_service.py:205+): indexes Client/Project/Website/Task/DBDocument as LlamaDocument text=summary + metadata {'entity_type', 'content_type':'entity_summary', searchable_content, ids/names}.
  - EntityContextEnhancer (entity_context_enhancer.py:26 enhance_query_context: extract mentions via regex (clients/projects/...), _get_entity_relationships (safe DB queries with limits/sanitization), _build_enhanced_context + relationship_summary).
  - Usage: enhanced_chat_api.py:2974 (get_entity_context_for_chat), 842 (universal RAG always pulls "entity_summary" chunks), bulk/generation/query_apis (enhance_query_context), seed_rules mention entity relationships.
  - Frontend: entityDetector.js, queryClassifier.js (ENTITY type boosts), utilService for links, usePageContext.js, EntityContextMenu, linking modals (projects/clients etc).
- **Other Context**: RAG (various retrieve), session history compaction, desktop state (live in unified_chat_engine + ACS), cli_working_memory, vision context utils, interface context.
- **Callers Unified**: unified_chat_engine (memory + desktop in _build_system_prompt), agent_brain (passes to ACS/executor), brain_state (memories in prompts), agent_executor (internal memory_mgr + session_context prepend), ACS (chat_context + lessons?).
- **Frontend Exposure**: MemoryManagementSection (full CRUD for AgentMemory incl lesson edit via LessonSummaryModal PATCH), Chat indicators, EnhancedChatInterface memoryActive, sessionStateService (context preservation across gens/resets).

### 3.2 Budget (StepBudget) as Current "Solidification" Pattern
- **Definition** (brain_state.py:123-193): dataclass total=20, used=0, history:List[Dict{tier,amount,reason,remaining_after}]; @property remaining; charge(amount,tier,reason) appends + returns bool (remaining>0); on_escalation; to_context() (human: "Cross-tier agentic step budget: used X/Y, Z remaining (urgency). Do not waste..."); to_llm_summary() concise "[BUDGET: rem/total steps left (pct% used)]"; to_telemetry(); from_total.
- **"Solidification" Language**: agent_brain.py:184 ("The budget is the 'solidification' of agentic constraints"); agent_control_service.py:463 ("The budget is the "solidification" mechanism: ... consistent inherited cap"); agent_executor.py:412 comment ("Make budget visible... for awareness/solidification").
- **Lifecycle + Injection (Cross-Tier)**:
  - Creation: mostly AgentBrain.process (187: from_total(TOTAL_STEP_CAP=20 or max_steps)); passed down.
  - Charges: Tier1 reflex 247, vision 270, conversational 281, deliberation signal 291, default t2 301, on_escalation 316, gemma 468, ACS/executor entry 600/368, per-iter ACS 614.
  - Visibility:
    - agent_brain _gemma4_direct: budget_aware_context = history + to_llm_summary() + note; pass budget to acs.execute_task.
    - agent_executor.execute: prepends to_llm_summary to session_context 414-416; _budget stored; in _build_system_prompt: budget_line 812-813 + in next_prompt budget_block 724-738; also in native path; facts + budget together in nudges.
    - agent_control_service.execute_task: _current_budget, charges/enforce in loop 607-614; _build_unified_prompt injects budget_block + chat_context at top of prompt 2174-2178 ("... (cross-tier budget — be efficient...)"); also _build_decision/vision etc (not all shown but pattern).
    - brain_state: to_* methods for live awareness.
  - Enforcement: min(max_iters, remaining); abort on <=0.
  - Telemetry: attached in brain (total_agent_steps, budget dict).
- **Siloed Aspects**: Only lives in agent/ACS paths (AgentBrain -> executor/ACS); transient (per-process dataclass, history lost on end); NO write to AgentMemory / lessons / entities / RAG; no tool exposure for "query my budget"; frontend only has Claude token budget bars (not step); no active querying; different "budget" concepts (token/VRAM/GPU quotas in claude_advisor, ollama_resource, config, tasks) not unified with StepBudget.
- **Visibility "Added"**: Comments + to_* calls + prepends show recent work on awareness, but only inside agent loops, not global context system.

### 3.3 Other Related (Apprentice, Reflexes, Self-Knowledge, Facts, Unified Progress)
- **ApprenticeEngine** (apprentice_engine.py): replays demos with autonomy (guided->supervised->autonomous); success_count promotes; used in agent_control_service for learning; ties to lessons/demos.
- **Reflexes** (brain_state.py _build_default_reflexes 238: media/greeting/etc zero-LLM; Tier1 in agent_brain 234; servo_knowledge_store proposes reflex updates from self-improv; tests).
- **Self-Knowledge**: data/agent/self_knowledge*.md + recipes.json (loaded in ACS?); belief_updates reconcile via lesson_reconciler to hedge claims; agent_control_api mentions long-form context there.
- **FactsRegistry** (agent_executor internal): ExtractedFact from obs (web_search/analyze/generic keyphrases); format_facts_for_prompt; used for synthesis/verify at end or mid-loop (no tool calls); cumulative across iters; NOT persisted beyond execution (siloed to deliberation).
- **Unified Progress**: Completely separate (utils/unified_progress_system.py, contexts/UnifiedProgressContext.jsx, job_registry adapt); tracks jobs/processes (not agent memory/context/budget). Injected in tasks/apis but orthogonal to LLM context.
- **Frontend Context Vars**: sessionStateService (continuity markers, backups for no memory reset), query/entity classifiers for injection, pageContext.entityType, smartContextBuilder (code budgets), memory UI for AgentMemory.

## 4. Specific Integration Proposals (Budget as Queryable Entity/Memory/Fact/Lesson + Unified Injection)
**Core Thesis**: Budget (StepBudget state + history + charges + remaining) is exactly the kind of "agentic state" that fits existing durable/queryable patterns (AgentMemory as fact/note/belief_update, entity_summary in RAG, lesson_summary for procedures, FactsRegistry for obs-derived). Currently siloed -> agent can't "remember" its own past budget discipline/efficiency across sessions or query "how much budget do I have left on this project?" Treating "self" (or "agent_budget") as first-class entity + persisting key charges as memories enables solidification (personality-level awareness, lessons like "budget <5 -> use reflex or short paths").

### 4.1 Treat Budget as Queryable "Fact" / Memory Type
- Extend memory_contract.py: add "budget_state" or "agent_state" to MEMORY_TYPES; source "auto" or "agent"; type "fact" or new "state_fact". Normalize in add/update paths (memory_api.py).
- On budget exhaustion / task end (agent_executor final, ACS finish, brain finally): persist summary via add_memory (from backend.api.memory_api import add_memory):
  - content: json or text "Budget used: X/Y (pct%). History summary: [last N charges]. Efficiency note: ...". source="agent", type="fact" or "belief_update", tags=["budget","step_budget", session/project], importance=0.7, metadata={"budget": budget.to_telemetry(), "session_id":...}.
  - Or as lesson_summary if procedural ("When remaining<3 prefer direct tools").
  - Line refs: memory_api.py:232 (add_memory call in lessons), agent_control_service:1612 (_write_session_lessons), executor:523 (final).
- Make budget queryable: enhance _query_memories / get_memories_for_context to accept types=["fact","budget_state"] or special filter; surface under "Agent self-knowledge / resource state" header (like facts).
- Active querying: add tool "get_agent_budget" or "query_self_state" (in unified_chat_engine CORE_TOOLS or agent_tools) that returns current (if in agent context) + recent persisted budget facts from memory_api. Mirrors search_memory.
- Entity angle: "agent_self" or "budget" as pseudo-entity. In entity_indexing_service, add synthetic "agent" entity doc with current budget snapshot (updated on events); enhance EntityContextEnhancer to recognize "my budget"/"agent steps" queries and pull relationships (e.g. budget vs past tasks). Frontend entityDetector + queryClassifier already handle ENTITY; extend for "self budget".

### 4.2 Unified Context Injection (Avoid Silos)
- Centralize budget awareness: in memory_api.py get_memories_for_context (or new get_agent_state_for_context), if budget provided or fetch recent, inject formatted block (reuse to_context/to_llm_summary) alongside memories. Update callers uniformly:
  - unified_chat_engine.py:2499 (add budget param or live from brain_state?); currently only memories+desktop.
  - brain_state.py get_system_prompt: already {MEMORY_BLOCK}; extend to include budget if in agent mode.
  - agent_brain / executor / ACS: continue prepend but source from shared formatter instead of duplicating to_* calls (reduce silo: executor 414, acs 2175, brain 469).
- Always-on injection: like always_on high-import facts (memory_api 613), surface recent budget_facts in every prompt (unified + agent paths).
- Cross-tier pass-through: StepBudget already excellent (history carried); persist the telemetry on session end (agent_control_api or unified_chat_api post) so next session's get_memories pulls "self budget history".
- Session/project scoping: budget memories inherit session_id/project_id (existing filters).
- In FactsRegistry style: during agent loops, "extract" budget charges as temporary facts (like obs facts 697) for mid-loop synthesis ("budget spent on web_search: 3 steps").
- Unified progress tie-in? Minor: emit budget events to unified progress for UI visibility (but keep separate from LLM context).

### 4.3 Agentic "Solidification" + Overlooked Features
- Persist budget history as memories: enables cross-session learning ("Last time budget hit 0 on long research, switched to Tier1 reflexes early").
- Entity relationships incl "self budget": Agent as entity linked to tasks/projects; budget state as attribute or related "resource" entity. RAG can retrieve "budget used on similar client projects".
- Lessons from budget usage: In lesson distiller (lessons_api.py) or self_improvement_tasks, detect high-spend paths and distill "Efficient budget use: for X use direct {tool} instead of multi-step".
- Active self-awareness: Tool "recall_self_budget" or extend reflexes to low-budget patterns. Expose in brain_state as queryable (like reflexes_count in brain_api.py:38).
- Frontend budget visibility: Extend Memory UI or add "Agent Budget" card (like Claude in UncleClaudeSection.jsx:177 or GpuStatus); surface in chat (like memoryActive); usePageContext or FloatingChat to show remaining if agentic session. DevTools entity indexing already there; add "index agent state".
- Overlooked existing: 
  - get_memories_for_context already has char/token budgets + grouping (perfect for injecting budget facts without overflow).
  - Entity summaries + enhancer already universal RAG (easy to add synthetic budget entity).
  - FactsRegistry + synthesis (extensible to budget "facts").
  - Lesson pearls + parameterized (budget lessons can use {remaining} placeholders?).
  - Reflexes (add low-budget reflex: "if budget tight -> short answer").
  - ApprenticeEngine (replay budget-efficient demos at higher autonomy).
  - belief_update / reconciler (budget contradictions like "thought I had 10 steps but hit 0" -> hedge self-knowledge).
  - chat_context/session_context already carry budget in agent paths (just wire to memory path for non-agent too).
  - access_count/last_accessed on memories (budget memories would auto-rank on "budget" queries).
- Gaps to close: No persistence (history lost); no non-agent budget awareness; no UI; token budgets (Claude) and step budgets not unified; no "self" entity; FactsRegistry not feeding durable memory; no tool for introspection on own limits.

## 5. Gaps & Risks
- **Silo Risk**: Budget awareness only in 3 agent files (dupe to_* logic); memories/lessons/entities are the "long-term brain".
- **Transience**: StepBudget history dies with process; agent can't learn "I tend to overspend on vision tasks".
- **Queryability**: No way for LLM or user to "SELECT budget facts" via existing memory tools/RAG.
- **Frontend/UX**: Entity/memory rich in UI, budget invisible (except Claude %); user can't see/curate "agent's budget lessons".
- **Overload**: Adding budget to every memory query needs careful truncation (already handled by char_budget).
- **Provenance**: Use existing AgentMemoryAudit + lesson_id for budget writes.
- Other budgets (VRAM, token) could follow same pattern later for "resource self-awareness".
- Tests: Existing test_memory_api_lessons.py, test_brain_state_memory_block.py cover injection; add for budget-as-memory.

## 6. Recommended Next Steps (Prioritized, File-Referenced)
1. Extend memory_contract + memory_api: new type/source for budget_state; helper persist_budget_state(budget, session_id, ...). (memory_contract.py:25, memory_api.py:670+ _query).
2. Wire persistence: in agent_executor.execute finally / ACS finish / brain finally: if budget: add_memory( formatted from to_telemetry() ). (agent_executor:530, agent_control_service:1665, agent_brain:338).
3. Unify injection: modify get_memories_for_context to accept/ merge budget; update 3 callers (unified_chat_engine:2494, brain_state:865, memory_api consumers). Or central context builder.
4. Add "budget" tool / reflex / entity: register get_current_budget (from brain or persisted); synthetic entity in entity_indexing_service.
5. Lessons integration: in _distill or self_improvement, extract budget lessons. (lessons_api:230, self_improvement_tasks:116).
6. Frontend: MemoryManagementSection already renders budget facts if typed; add AgentBudget panel using new /memory?type=budget_state or dedicated endpoint; expose in chat status. (frontend/src/components/settings/MemoryManagementSection.jsx:400+, ChatPage).
7. Telemetry + UI: surface budget.to_telemetry() in existing brain telemetry; add to UnifiedProgress? sparingly.
8. Test + migrate: extend test_memory_api_lessons; deprecate direct to_llm_summary prepends in favor of memory path.

This makes the agent contextually grounded w.r.t its own resource limits — "solidification" complete. All proposals reuse existing rich infrastructure (no new silos).

**References Summary (key files/lines)**:
- StepBudget core: backend/services/brain_state.py:123-193
- Injection sites: agent_brain.py:469, agent_executor.py:414/724/812, agent_control_service.py:2174 (and 601)
- Memory injection: backend/api/memory_api.py:670 (get_memories_for_context), 823 (get_lessons), 503 (_query)
- Facts: agent_executor.py:80 (FactsRegistry)
- Entities/RAG: backend/utils/entity_context_enhancer.py:19, backend/services/entity_indexing_service.py:205 (entity_summary)
- Lessons: backend/api/lessons_api.py:49 (_distill), backend/services/lesson_reconciler.py:168 (scan)
- Unified engine: backend/services/unified_chat_engine.py:2494 (mem), 2478 (prompt), 914 (build)
- Contract: backend/services/memory_contract.py:25
- Model: backend/models.py:2463 (AgentMemory)
- UI: frontend/src/components/settings/MemoryManagementSection.jsx, frontend/src/utils/entityDetector.js + queryClassifier.js
- Apprentice/Reflex/Self: apprentice_engine.py:42, brain_state.py:238, tasks/self_improvement_tasks.py:116

Exhaustive per instructions. Ready for implementation planning.