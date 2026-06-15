# Agentic Awareness Specialist Findings — Phase 2 Budget & Self-State Solidification

**Role:** Agentic Awareness Specialist  
**Date:** 2026-06-14  
**Workspace:** /home/llamax1/LLAMAX8  
**Task Focus:** Make budget (and agent self-state) actively queryable + visible for real personality/awareness/solidification. Tie into existing "see + respect" wiring (prompt markers + enforcement). Analyze mechanisms; propose designs for "active query_budget" reflex/tool + passive enhancements. Output detailed findings + concrete sketches + Phase 2 recs. Save to regroup-reports/AWARENESS_SPECIALIST_FINDINGS.md.

**Sources read (via list_dir + read_file + grep; full or targeted reads with offsets):**
- backend/services/agent_brain.py (full + offset 1000)
- backend/services/brain_state.py (full)
- backend/services/agent_executor.py (full + offset 1000)
- backend/services/agent_control_service.py (targeted: 1-400, 430-480, 2100-2400+, grep for budget/_current_budget/get_status)
- backend/api/memory_api.py (targeted: 1-100, 660-820+ for get_memories_for_context + lessons)
- backend/services/unified_chat_engine.py (targeted: 1-150, 650+, 900+, 2470+ for CORE_TOOLS/prompt build + injections)
- backend/services/apprentice_engine.py (1-100)
- backend/tools/agent_control_tools.py (288-300 for AgentStatusTool)
- backend/services/lesson_reconciler.py (1-50)
- backend/api/brain_api.py (full)
- backend/services/agent_tools.py (45-90 for BaseTool)
- backend/services/memory_contract.py (1-80)
- backend/utils/entity_context_enhancer.py (1-100)
- backend/tools/tool_registry_init.py (650-680 for registration)
- grep across backend/ for: agent_brain|StepBudget|budget|to_llm_summary|to_context|FactsRegistry|EntityContextEnhancer|entity_context|apprentice|lesson|reflex|ReflexAction|agent_status|get_memories_for_context|CORE_TOOLS
- regroup-reports/ (existing reports for output style; dir listing)
- Related: backend/services/ (memory patterns, lessons_api.py via grep), tools/ patterns for BaseTool impls.

**Cross-refs to user directive context (handoff/prior work):** agent_brain + StepBudget + visibility, "see + respect" (prompt markers + enforcement), FactsRegistry/EntityContextEnhancer in executor, memory_api + get_memories_for_context, lessons/apprentice_engine, reflexes in brain_state, unified_chat_engine, agent_executor + agent_control_service (prompt injections), hardware_policy etc. (out of scope here).

---

## Executive Summary

The StepBudget (introduced as "first-class cross-tier termination budget" + "solidification of agentic constraints") is **already wired for enforcement ("respect") and passive visibility ("see")** in agentic paths (Tier 3 executor, Gemma-direct/ACS, some Tier 2). Code comments in agent_brain.py:184-186, brain_state.py:131-133, executor:412-416, acs:463-464 explicitly call for making the agent *aware* ("personality-level caution", "develops real personality", "visible to you for awareness").

**Current state (gaps vs goal):**
- **Passive "see" is partial + uneven:** to_llm_summary() / to_context() injected into system/next prompts, session_context, chat_context, THINK templates in executor/acs/gemma paths (with markers like "[BUDGET: rem/total (pct% used)]", urgency suffixes, "be efficient", "cross-tier inherited; track your spend", "BUDGET IS LOW"). Enforcement: charge() on every tier/iter/entry/escalation + hard remaining<=0 aborts + iters capped by budget.remaining. Telemetry attaches full budget state.
- **Not in all paths:** No injection in core unified_chat_engine (Tier 2 default path; only charging in agent_brain._instinct wrapper). Not surfaced in brain_state.get_system_prompt (only MEMORY_BLOCK/DESKTOP_STATE live subs).
- **Active querying is missing:** No "query_budget" tool or reflex. Existing "agent_status" (CORE_TOOLS, always offered, cheap introspection; implemented in agent_control_tools.py:288-299 calling acs.get_status()) reports vision state (active/iteration/history/last_result) but **zero budget**. Other status tools (media_status, outreach_status) exist as patterns. Reflexes (Tier1, brain_state:238+) are static/pattern-based (media/greetings), no budget access. 
- **Not first-class in awareness systems:** FactsRegistry (executor:80-286 only; extracts from web/analyze/generic tool obs into [Fact N] for synthesis/_verify/_next_prompts) ignores budget. No feeding of budget events. get_memories_for_context (memory_api:670+) surfaces facts/notes/preferences/lesson_summary (grouped + SECTION_HEADERS; called live from brain_state + unified_chat_engine) — budget never written as memory. EntityContextEnhancer (utils/) for relationship summaries (not applicable directly). Lessons (lessons_api + lesson_reconciler + apprentice_engine): distill belief_update/lesson_summary from contradictions/experiences into durable AgentMemory (source trust, flattened for prompt); reconciler proposes knowledge file edits. No budget exhaustion lessons.
- **Self-state incomplete:** BrainState/brain_api expose model_caps/health/reflexes_count/tools_count/system_prompts (no budget). agent_status is vision-specific. No unified "self-state" queryable by agent for personality (e.g. "I am budget-conscious").
- **Result:** Tools/capability exist but "feel/personality/awareness" broken — agent burns steps until hard-kill instead of internalizing "I have limited effort; plan short; query my limits; respect markers".

**"See + respect" already started (ties in):** Prompt markers + explanatory text ("visible to you", urgency) + enforcement (charges/checks/aborts) in the exact locations noted in prior agentic work. Phase 2 must extend this to active + memory/facts/lessons for solidification.

**Bottom line:** Budget is a latent "self" signal. Making it queryable (tool + reflex) + feeding it (FactsRegistry + memories + lessons on exhaustion) + broader passive injection will let the model develop "cautious/efficient personality" (e.g. prefer Tier1 reflexes, short paths, explicit status checks before multi-step).

---

## Detailed Code Analysis (Specific Existing Patterns)

### 1. StepBudget Definition & API (brain_state.py:123-193)
```python
@dataclass
class StepBudget:
    total: int = 20
    used: int = 0
    history: List[Dict] = ...
    @property remaining: int
    def charge(self, amount, tier, reason) -> bool: ...  # mutates + appends history; returns remaining>0
    def on_escalation(...)
    def to_context(self) -> str:  # "Cross-tier... used X/Y, Z rem (BUDGET IS LOW...)"
    def to_llm_summary(self) -> str:  # "[BUDGET: rem/total (pct% used)]"
    def to_telemetry(self) -> Dict
    @classmethod from_total(cls, total)
```
- Explicitly designed for awareness (comments:131-133: "exposed as a tool / in system prompt").
- History for debugging; pct/urgency for LLM.
- **Usage pattern:** Created in agent_brain.process:187 (TOTAL_STEP_CAP=20), charged everywhere (tier entries, reflex:247, vision:270, escalation:316, gemma:468, executor entry:368, acs per-iter:614), passed explicitly (preferred over legacy max_steps).

### 2. Injection Points ("See" Wiring) — Passive Visibility
- **agent_brain.py:**
  - process:182 comment: "Explicit first-class... solidification... agent should eventually be made aware".
  - _gemma4_direct:469-470: `budget_aware_context = ... + budget.to_llm_summary() + " (cross-tier budget visible to you...)"`; passed as chat_context + budget= to acs.execute_task.
  - _deliberate:1070-1073: `if budget: session_context += f"\n{budget.to_context()}"`; passed to executor.
  - Charges + passes budget to _instinct/_deliberate everywhere (e.g. 225,291,301,316).
- **agent_executor.py:**
  - execute:369: `self._budget = effective_budget`
  - 412-416: `if getattr(self,'_budget'): budget_summary = "\n" + self._budget.to_llm_summary() + ...; session_context = budget_summary + ...`
  - _build_system_prompt:811-813: `budget_line = "\n" + self._budget.to_llm_summary() + " Use this... plan efficiently" ` (in base + vision rules).
  - _execute_iteration:724-726: `budget_block = self._budget.to_llm_summary() + "\n\n"`; prepended to next_prompt (also at 738).
  - Similar (lighter) in _execute_iteration_native.
  - FactsRegistry: self.facts_registry (305); extract on every tool result (697,1007); format_facts_for_prompt() injected to nudges/synthesis (596,713, etc.).
- **agent_control_service.py:**
  - execute_task:601: `self._current_budget = effective_budget`
  - 606-614: per-iter check `if ...remaining <=0: ... "budget_exhausted"`; charge(1,3,"acs iteration").
  - _build_vision_prompt (~2174-2176): `budget_block = self._current_budget.to_llm_summary() + " (cross-tier budget — be efficient... visible to you for awareness.)\n\n"`; injected at head of master prompt (2178).
- **unified_chat_engine.py (Tier2 path):** No budget injection (only memory_block + desktop_block in _build_system_prompt:2492+; tool selection always includes CORE_TOOLS). agent_brain._instinct charges but does not forward budget= to engine.chat.
- **brain_state.py get_system_prompt:** Live subs for {MEMORY_BLOCK} (via get_memories_for_context:865), {DESKTOP_STATE} (via acs._get_desktop_state). No budget placeholder.
- **Telemetry/final:** brain:352 attaches budget; always present in logs.

### 3. Enforcement ("Respect" Wiring)
- Hard: iters = min(..., budget.remaining); if <=0 abort (acs:608, executor:366).
- Soft via prompts: urgency text, "Do not waste steps", "track your spend".
- Cross-tier inheritance: one budget object flows Reflex->Instinct->Delib/Gemma/ACS/Executor.

### 4. Active Query Mechanisms (Existing Patterns to Extend)
- **Tools (agentic active call pattern):**
  - agent_status (agent_control_tools.py:288): `name="agent_status"; description=...; execute: acs = get...; return service.get_status()`. Always in CORE_TOOLS (unified_chat_engine:145,432,711 etc. — "cheap introspection — agent should always be able to report its state"; excluded from some padding but forced in).
  - Similar: media_status, outreach_status (status snapshot pattern).
  - Memory tools (save_memory/search_memory/delete_memory in CORE; implemented memory_tools.py) — agent can actively persist/query facts.
  - BaseTool (agent_tools.py:45): name/desc/params/execute->ToolResult; is_dangerous/requires_approval; set_context; registered in tool_registry_init.py + agent_tools.py filters.
  - get_status (acs:433): returns dict active/ready/iteration/history/last_result (no budget/_current_budget exposed).
- **Reflexes (Tier1 active, zero-LLM):**
  - brain_state:238 `_build_default_reflexes`: pattern lists + handler(msg, match, ctx) -> ReflexResult (response or tool_called). Examples: media_play (calls tool_registry), greeting rotation. match_reflex:826 (used in brain:235). Sorted by priority. Context-free (no budget/history).
  - In agent_brain:236-247: if match, execute handler, charge(1,1), return early.
- **Memory queries (active + passive):**
  - get_memories_for_context (memory_api:670): queries _query_memories, groups by type/source (lesson_summary special), renders with SECTION_HEADERS + truncation. Called live (brain_state:865, unified:2494). Lessons flattened (json -> "LESSON (title): 1. step | PARAMETERS...").
  - save/search/delete_memory tools allow agent to actively curate.
- **Facts extraction (executor-only active synthesis):**
  - FactsRegistry: extract_facts_from_observation (tool-specific for web/analyze; generic str/dict phrases; thread-safe). format_facts_for_prompt() -> "[Fact id] content\n  Source: ...\n". Used in next_prompts (executor:603,713,937), synthesis (1089), verify (1127). Skips some (e.g. edit_code per comment 111).
- **Entity patterns:** EntityContextEnhancer.enhance_query_context (relationship_summary etc.); used in enhanced_chat/generation APIs for "ENTITY CONTEXT" blocks. Analogy: treat budget as "self-entity".
- **Lessons/apprentice (durable learning):**
  - apprentice_engine.py: graduated autonomy replays (not directly budget).
  - lessons_api.py: start/end bracket, _distill_lesson_pearls -> AgentMemory(source="lesson_summary", type="lesson").
  - lesson_reconciler: groups belief_update memories -> PendingFix for knowledge files (self_knowledge*.md, recipes.json). Sources: "agent", "lesson_summary", "belief_update" (memory_contract:45+).
  - get_lessons_for_agent_prompt (memory_api:823) for ACS knowledge.
- **Other self-state:** BrainState singleton (model_caps, health, reflexes, tool_registry); brain_api /health + /telemetry expose counts but no runtime budget. MCP/redis tools (external to this).

**Analysis of integration opportunities:** Budget can be "first-class" by (a) exposing via existing introspection tool (agent_status), (b) new tool/reflex for explicit "query_budget", (c) treat charges/exhaust as "observations" -> FactsRegistry (like tool results), (d) persist key events as memories/lessons (like belief_updates), (e) always-inject like MEMORY_BLOCK/DESKTOP_STATE. This mirrors "see (prompts) + respect (enforce) + remember (facts/memories)" loop already in vision/agent paths.

---

## Proposed Concrete Designs

### Design A: "active query_budget" Reflex + Tool (Primary Recommendation)
**Goal:** Agent can *decide* to call "what's my remaining budget?" mid-reasoning (active awareness) before expensive actions. Ties to CORE_TOOLS pattern.

1. **Enhance existing agent_status (minimal change, reuse pattern):**
   - acs.get_status (agent_control_service.py:433): append
     ```python
     budget_info = {}
     if getattr(self, '_current_budget', None):
         b = self._current_budget
         budget_info = b.to_telemetry() | {"summary": b.to_llm_summary(), "context": b.to_context()}
     status["budget"] = budget_info or {"status": "no_active_cross_tier_budget"}
     # Also pull from brain_state if available for model/self basics
     ```
   - Similarly executor: expose self._budget in a get_current_budget_snapshot().
   - Update AgentStatusTool description: "Get the current status of the agent vision control system, *including cross-tier step budget if active in this execution*. Use for self-awareness of limits."
   - Result: agent calls agent_status -> sees budget live. (Already always-available in CORE.)

2. **Dedicated QueryBudgetTool (new, first-class; in agent_control_tools.py or new services/budget_awareness.py):**
   ```python
   # Sketch (implement parallel to AgentStatusTool)
   class QueryBudgetTool(BaseTool):
       name = "query_budget"
       description = ("Actively query your current cross-tier agentic step budget (remaining/total/used/pct/history summary). "
                      "Returns machine-readable + LLM summary string. Call this explicitly when planning multi-step work, "
                      "before escalation, or when you feel uncertain about effort left. Use to develop efficient personality.")
       parameters = {}  # or optional "include_history": bool
       def execute(self, **kwargs) -> ToolResult:
           try:
               # Preferred: context from executor/acs (via set_tool_context or threadlocal)
               # Fallback: try acs (for vision tasks), or brain singleton snapshot
               from backend.services.agent_control_service import get_agent_control_service
               svc = get_agent_control_service()
               if getattr(svc, '_current_budget', None):
                   b = svc._current_budget
                   return ToolResult(success=True, output={
                       "summary": b.to_llm_summary(),
                       "details": b.to_telemetry(),
                       "advice": "Low remaining? Prefer direct tools/reflexes and short paths."
                   })
               # ... try executor global or last-known
               return ToolResult(success=True, output={"status": "no_active_budget_tracked_in_this_context", "default_total": 20})
           except Exception as e:
               return ToolResult(success=False, error=str(e))
   ```
   - Register in tool_registry_init.py (around agent control block) + agent_tools.py vision/agent filters if needed.
   - Force into CORE_TOOLS in unified_chat_engine.py (add "query_budget").
   - Make executor/acs push budget to tool context: e.g. in execute: `if budget: tool_registry.set_context_for_session(session, budget=budget)` or use the existing agent_context mechanism.

3. **Reflex for ultra-cheap awareness (brain_state.py _build_default_reflexes):**
   ```python
   # Add (priority high so early)
   reflexes.append(ReflexAction(
       name="budget_query",
       patterns=[re.compile(r"(?i)(budget|steps? left|remaining steps?|how many (steps|left)|effort left|my limit)")],
       handler=lambda msg, match, ctx: ReflexResult(
           response="Use the query_budget or agent_status tool for live cross-tier budget. Default cap is 20 steps total across tiers.",
           success=True,
       ),
       priority=5,
   ))
   ```
   - Reflexes are context-free; this gives immediate "see tool" nudge without LLM. Handler can be extended if we add a static default or app-global last_budget (non-ideal).

**Active call flow:** LLM in Tier2/3/ACS sees tool in list (or from prior prompt), outputs <tool>query_budget</tool> (or JSON), registry executes, result fed back as observation (like any tool) -> can go to FactsRegistry.

### Design B: Integrate Budget into FactsRegistry + Synthesis (Executor Awareness)
- Extend FactsRegistry (agent_executor.py):
  ```python
  def add_budget_fact(self, budget: StepBudget, iteration: int, event: str = "update"):
      if not budget: return []
      fact = ExtractedFact(
          content=f"Budget {event}: {budget.to_llm_summary()} (used {budget.used}/{budget.total}, {budget.remaining} rem). History summary: {len(budget.history)} charges.",
          source_tool="budget_monitor",  # or "system"
          confidence=1.0,
          iteration=iteration,
          raw_evidence=str(budget.to_telemetry()),
      )
      # thread-safe append logic (copy from extract_...)
      ...
  ```
- Call sites: in executor.execute after charge (368), on every _execute_iteration entry, before final (or on exhaustion path: "budget_exhaustion_event").
- In ACS: on charge/exhaust, if facts_registry accessible or mirror simple fact log.
- Benefit: budget status becomes citable [Fact N] in synthesis/verify/next_prompts — model "remembers" its own spend during run (like tool obs).

### Design C: Passive Enhancements + Broader "See"
- **unified_chat_engine.py:** Modify chat() and _build_system_prompt to accept/ use `budget: Optional[StepBudget]=None`. In context_parts or after desktop_block:
  ```python
  if budget:
      context_parts.append(budget.to_llm_summary() + " (cross-tier step budget — respect limit, be efficient, prefer cheap paths like reflexes or agent_status query).")
  ```
  Forward from agent_brain._instinct: pass budget=budget to engine=...; engine.chat(..., budget=budget).
- **brain_state.py:** Add optional budget param to get_system_prompt; append live block (mirrors {MEMORY_BLOCK} pattern). Or always query a "current context budget" if we add lightweight per-session tracking.
- **acs/executor prompts:** Already good; enrich budget_block when remaining low (e.g. append "CRITICAL: finalize with what you have.").
- **brain_api.py + BrainState:** Add to /health: "supports_budget_awareness": True, and perhaps last_budget_snapshot (if we track in state). Expose get_self_state() aggregating model + health + reflexes + budget (if active).
- **Desktop/memory analogy:** Treat budget as live "self-state block" substituted in all system prompts.

### Design D: Lessons & Memory for Budget Exhaustion / Efficiency (Long-term Personality)
- On "budget_exhausted" returns (acs:611, similar in executor max-iter paths): 
  ```python
  # In finish paths or agent_brain telemetry
  try:
      from backend.api.memory_api import _write... or direct AgentMemory
      # Or better: call the save_memory *tool* logic for provenance
      mem = AgentMemory(..., type="note", source="agent", content=f"Budget exhaustion event: task exhausted {used}/{total} steps. Lesson: use query_budget early; prefer direct reflexes for simple queries. Context: {reason}")
      ...
      # Tag as "belief_update" or "efficiency" for reconciler/lessons flow
  ```
- Use lesson bracketing (lessons_api) for "budget efficiency training" sessions.
- Then: get_memories_for_context surfaces under "Operating notes" or "Learned procedures". Reconciler can promote to knowledge files (e.g. "LEARNING_PRINCIPLES.md").
- Apprentice: replay demos that were budget-efficient vs wasteful.
- Fact: "agent" source trust (memory_contract:70) + DEFAULT_IMPORTANCE.

### Design E: Self-State Unification
- New/expanded: `query_self_state` tool (or augment agent_status + query_budget) that pulls:
  - From BrainState: active_model, model_caps (vision/thinking), health, reflexes_count, lite_mode.
  - From acs/executor: current budget + vision active/iteration.
  - From memory: recent lessons.
- Description: "Comprehensive self-introspection for personality and awareness (budget, capabilities, state, learned lessons)."
- Always CORE. Use in initial system prompts or as reflex target.

---

## Implementation Sketches (Ready-to-Code Diffs)

**Sketch 1: Expose budget in acs.get_status + agent_status (quick win, ~15 LOC)**
- Edit agent_control_service.py:439 (in get_status dict): add the budget_info block above.
- Edit agent_control_tools.py:290 description update + execute remains same (now richer output).
- Bonus: also surface in executor if vision not active.

**Sketch 2: Add query_budget tool (new file or append agent_control_tools.py)**
- Implement class as in Design A (copy AgentStatusTool structure).
- In tool_registry_init.py: after AgentStatusTool register: `register_tool(QueryBudgetTool()); registered.append("query_budget")...`
- In unified_chat_engine.py CORE_TOOLS lists (3 places): add "query_budget".
- In agent_brain/executor/acs: on budget creation/assign, call a `set_current_budget(budget)` helper that tools can read (e.g. via module-level _current or better: attach to thread-local or pass in agent_context that execute_tools forward).

**Sketch 3: Inject in unified_chat + engine (passive Tier2 fix)**
- Add `budget: Optional["StepBudget"] = None` to UnifiedChatEngine.__init__ and .chat sig.
- In _build... and context assembly: the budget_block append (copy pattern from acs:2174 or executor:415).
- Callsite update in agent_brain.py:954 `UnifiedChatEngine(..., budget=budget)` + pass in engine.chat(..., budget=budget) (add kwarg).

**Sketch 4: FactsRegistry budget facts**
- In FactsRegistry: add `add_budget_fact` method (~20 LOC copy of extract).
- Call after key charges: `self.facts_registry.add_budget_fact(self._budget, iteration, "charge")` (in executor loop, gemma/acs if registry reachable, or simple global mirror).
- Existing format_facts will now include budget facts in CUMULATIVE FACTS blocks.

**Sketch 5: Budget lesson on exhaust (memory_api + acs/executor)**
- Helper in memory_api or a new budget_awareness.py: `record_budget_event(used, total, reason, session_id=None)`.
- Invoke in acs:611 branch and executor max-iter: `record...("exhausted", ...)`.
- Content normalized per memory_contract (type="note" or "belief_update", source="agent").

**Sketch 6: Reflex addition (brain_state.py)**
- In _build_default_reflexes: the budget_query ReflexAction (as above). Rebuild on refresh (already wired).

**Registration/visibility:** All new tools auto in schemas (tool_registry.get_tool_schemas); forced in CORE; reflexes compiled at init.

**Testing hooks:** Existing tier_telemetry already logs budget; extend agent executor tests; add to brain health.

**Edge cases:** No budget (legacy/Tier1 convos) -> graceful "default 20, not tracked live". Threading (ACS is threaded) -> use task-local or explicit pass (already done for _current_budget). Multi-session -> per-execution object is correct (don't globalize).

---

## Recommendations for Phase 2 Sweep/Refactoring

1. **Priority 1 (solidify "see + active"):** Implement Sketch 1+2+3 (agent_status enrichment + new query_budget tool + unified_chat injection). This makes budget "actively queryable" with 1-2 days work. Update descriptions/COMMENTs everywhere to reinforce personality goal.

2. **Priority 2 (facts + memory wiring):** Sketches 4+5. Integrate budget into FactsRegistry (makes it "remembered" like tool results) + auto-record exhaustion as note/belief (feeds lessons/get_memories). Extends existing executor/acs/memory patterns without new singletons.

3. **Priority 3 (reflex + self-state):** Sketch 6 + expanded query_self / BrainState.get_self_state(). Add to brain_api/health. Unifies "agent self" (model + budget + state + lessons).

4. **Broader Phase 2:**
   - Standardize injection: introduce {BUDGET_BLOCK} placeholder in brain_state system_prompts (like MEMORY/DESKTOP); fill live.
   - Thread/context propagation for budget: formal "AgentContext" dataclass carrying budget + session + facts_registry handle (used by tools/executor/acs).
   - Exhaust lessons + reconciler: treat budget events as first-class belief_updates for efficiency rules in data/agent/*.md.
   - Visibility in more places: socketio/brain_api telemetry dashboards; frontend agent status widget shows live budget.
   - Personality metrics: extend TierTelemetry + add "budget_respecting_behaviors" (e.g. explicit query_budget calls before heavy loops).
   - MCP/Redis tie-in (per connected tools): if relevant, expose query_budget as MCP resource for external agents.
   - Docs: update CAPABILITIES.md, LEARNING_PRINCIPLES.md, agent self-knowledge with "I monitor my step budget via query_budget and respect it for efficiency."
   - Tests: extend test_agent_executor, test unified paths, add reflex match tests; verify injection in prompts via debug logs.
   - Avoid over-global: keep budget per-execution (avoids race conditions in concurrent chats).

5. **Risks/Mitigations:** Budget object lifetime (tied to process() call) — use explicit passing (current strength). Tool context for live reads — extend set_tool_context pattern. Over-injection token bloat — keep to_llm_summary concise (already is).

6. **Success Criteria (measurable):** Agent spontaneously calls query_budget/agent_status in >30% multi-step traces (from telemetry); lower avg used/total in logs; exhaustion events produce memory rows; prompt diffs show consistent [BUDGET:] markers + facts in all tiers; "personality" anecdotes in chat (e.g. "I only have 4 steps left so I'll use the fast reflex").

This completes the "more architecturally and contextually aware team" goal for budget/self-state. Existing "see+respect" is the foundation; these changes wire active querying + learning loops for true solidification.

**Files to touch in Phase 2 (absolute paths):**
- /home/llamax1/LLAMAX8/backend/services/brain_state.py (reflex + optional get_self_state)
- /home/llamax1/LLAMAX8/backend/services/agent_brain.py (pass budget to engine)
- /home/llamax1/LLAMAX8/backend/services/agent_executor.py (FactsRegistry extension + calls)
- /home/llamax1/LLAMAX8/backend/services/agent_control_service.py (get_status + budget push)
- /home/llamax1/LLAMAX8/backend/services/unified_chat_engine.py (injection + CORE_TOOLS)
- /home/llamax1/LLAMAX8/backend/tools/agent_control_tools.py (QueryBudgetTool + AgentStatus update)
- /home/llamax1/LLAMAX8/backend/tools/tool_registry_init.py (register)
- /home/llamax1/LLAMAX8/backend/api/memory_api.py (record helper + get_lessons updates?)
- /home/llamax1/LLAMAX8/backend/api/brain_api.py (expose more self-state)
- /home/llamax1/LLAMAX8/backend/services/memory_contract.py (if new type needed)

**Next:** Implement sketches, run targeted tests (agent executor, brain, unified), update telemetry to count explicit budget queries. Re-group after for full personality sweep (e.g. other self-state like token usage, model confidence).

*End of Awareness Specialist report.*