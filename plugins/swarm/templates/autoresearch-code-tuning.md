# Swarm Plan: Autoresearch Code-Tuning Run

Karpathy-style overnight experimentation on Guaardvark itself. Each arm is a
coding agent in an ISOLATED git worktree off the run branch
`autoresearch/run-{RUN_TAG}`. An arm makes ONE focused change, proves it with
the test gate, and reports its result to the experiment ledger. Code never
merges to main automatically — the human reviews the run branch in the
morning.

Ground rules for every arm (ARIS/Karpathy DNA):
- ONE experiment per arm: a single, describable change with a hypothesis.
- The eval/test harness is ground truth. NEVER edit tests to make them pass;
  never edit the eval harness at all (`backend/services/rag_eval_harness.py`
  and `backend/tests/` assertions are read-only for you).
- Simplicity criterion: a tiny gain that adds ugly complexity is a discard.
  Deleting code and staying equal is a keep.
- You do not judge your own success — report the raw numbers; the judge agent
  scores independently.
- Report EVERY outcome (keep, discard, crash) to the ledger:
  `curl -s -X POST http://127.0.0.1:5000/api/autoresearch/experiments -H 'Content-Type: application/json' -d '{"run_tag": "{RUN_TAG}", "parameter": "<short-change-name>", "new_value": "<one-line description>", "status": "<keep|discard|crash>", "composite_score": <score-or-0>, "hypothesis": "<why you tried it>", "source": "code_arm"}'`
- Redirect noisy command output to files; read back only the lines you need.

## Task: Propose experiment slate
- files: docs/local-workspace-only/autoresearch-slate-{RUN_TAG}.md

Read `data/rag_research_program.md`, the last run's ledger
(`GET /api/autoresearch/metrics?limit=50`), and the current RAG retrieval
implementation (`backend/services/indexing_service.py`,
`backend/services/rag_eval_harness.py` — read-only). Write a slate of 2–3
SMALL, independent, testable code improvements to retrieval quality or eval
speed (e.g. a smarter chunk-neighbor expansion, a better BM25 tokenizer, a
cheaper dedup pre-filter). For each: hypothesis, files to touch, how the test
gate will prove it. Do NOT implement anything in this task.

## Task: Implement experiment arm A
- depends_on: propose-experiment-slate

Implement slate item 1 in this worktree. Run the relevant test files
(`python3 -m pytest backend/tests/test_autoresearch_integration.py -q` plus
any test file covering the code you touched) — ALL must pass or you fix or
revert. Then report to the ledger per the ground rules and commit with
message `experiment({RUN_TAG}): <change-name>`.

## Task: Implement experiment arm B
- depends_on: propose-experiment-slate

Implement slate item 2, same rules as arm A.

## Task: Judge and summarize
- depends_on: implement-experiment-arm-a, implement-experiment-arm-b

You are the independent judge — you did NOT write these changes. For each
arm: read its diff cold (`git diff`), verify its ledger claim matches what
the code actually does, check the tests it cites really cover the change,
and score keep/discard on the simplicity criterion. Write the verdict table
to `docs/local-workspace-only/autoresearch-verdict-{RUN_TAG}.md`. Flag any
arm whose ledger claim you could not reproduce — that is a finding, not a
formality.
