/**
 * Plain-language labels for the Autoresearch page and its dashboard card.
 *
 * Both read the same backend fields (/api/autoresearch/status and /runs), so
 * the wording lives here once: a run that refused to start says why, scores
 * show the measured baseline against the latest result (which may be lower),
 * and pytest health-check rows are never shown as kept experiments.
 */

const REASON_TEXT = {
  ollama_unreachable: "Ollama is not running",
  insufficient_corpus: "not enough indexed documents",
  no_eval_pairs: "no eval questions",
  eval_sources_not_indexed: "the eval questions come from documents that are not indexed",
  judge_unset: "no separate judge model is set",
  judge_same_as_answer_model: "the judge model is the answer model",
  celery_unreachable: "the task queue is unreachable",
  baseline_eval_failed: "the baseline could not be measured",
  prerequisite_check_failed: "the start checks could not run",
  codebase_locked: "the codebase is locked",
  self_improvement_disabled: "self-improvement is off",
};

const HEALTH_STATUS = { keep: "pass", discard: "fail" };

function asNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** A score to three places, or an em dash when there is none. */
export function formatScore(value) {
  const n = asNumber(value);
  return n === null ? "—" : n.toFixed(3);
}

/** A signed change ("+0.100", "-0.300"), or "" when either side is missing. */
export function formatDelta(value, from) {
  const a = asNumber(value);
  const b = asNumber(from);
  if (a === null || b === null) return "";
  const d = a - b;
  return `${d >= 0 ? "+" : "-"}${Math.abs(d).toFixed(3)}`;
}

/** The machine code at the start of a backend reason ("judge_unset — …"). */
export function reasonCode(reason) {
  const match = /^[a-z_]+/.exec(String(reason || "").trim());
  return match ? match[0] : "";
}

/**
 * A run's halt reason in plain words. Several reasons arrive joined by "; ";
 * an unknown code is shown as the backend wrote it.
 */
export function describeReasons(haltReason) {
  const parts = String(haltReason || "")
    .split("; ")
    .map((p) => p.trim())
    .filter(Boolean);
  if (parts.length === 0) return "no reason recorded";
  return parts.map((p) => REASON_TEXT[reasonCode(p)] || p).join("; ");
}

/**
 * What a research run produced, for a table cell or a one-line summary.
 * kind: "none" | "not_run" | "active" | "done". text is the visible line;
 * detail is the full backend wording for a tooltip.
 */
export function runOutcome(run) {
  if (!run) return { kind: "none", text: "—", detail: "" };
  if (run.status === "failed_precondition") {
    return {
      kind: "not_run",
      text: `Not run: ${describeReasons(run.halt_reason)}`,
      detail: run.halt_reason || "",
    };
  }
  const base = asNumber(run.baseline_score);
  const latest = asNumber(run.latest_score);
  const best = asNumber(run.best_tried_score);
  const kind = run.status === "running" || run.status === "pending" ? "active" : "done";
  if (base === null && latest === null) {
    const text = run.status === "pending" ? "Starting…" : "No baseline measured";
    return { kind, text, detail: run.halt_reason || "" };
  }
  let text = `${formatScore(base)} → ${formatScore(latest)}`;
  if (best !== null) text += ` (best ${formatScore(best)})`;
  return { kind, text, detail: run.halt_reason ? `Halt: ${run.halt_reason}` : "" };
}

/**
 * How to show one ledger row. Health checks (proposal_source "heal") are
 * pytest snapshots, not experiments; rows a code arm posted for itself are
 * self-reported, not measured by the eval harness.
 */
export function experimentLabel(exp) {
  const metrics = (exp && exp.retrieval_metrics) || {};
  if (exp && exp.proposal_source === "heal") {
    const outcome = HEALTH_STATUS[exp.status] || exp.status || "unknown";
    return {
      kind: "health",
      status: `health check: ${outcome}`,
      source: "health check",
      tone: outcome === "fail" ? "warning" : "default",
      selfReported: false,
    };
  }
  const selfReported =
    Boolean(metrics.self_reported) || (exp && exp.proposal_source === "code_arm");
  const status = (exp && exp.status) || "—";
  return {
    kind: selfReported ? "self_reported" : "experiment",
    status,
    source: selfReported ? "self-reported" : (exp && exp.proposal_source) || "—",
    tone: status === "keep" ? "success" : status === "crash" ? "error" : "default",
    selfReported,
  };
}

/** "YYYY-MM-DD HH:MM" in local time for a naive-UTC backend timestamp. */
export function formatMeasuredAt(iso) {
  if (!iso) return "";
  const text = String(iso);
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(text) ? text : `${text}Z`);
  if (Number.isNaN(date.getTime())) return text;
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** "Baseline: 2.394 (measured 2026-10-07 01:09)" or "Baseline: not measured". */
export function baselineText(status) {
  const score = asNumber(status && status.baseline_score);
  if (!score) return "Baseline: not measured";
  const at = formatMeasuredAt(status.baseline_measured_at);
  return `Baseline: ${score.toFixed(3)} (${at ? `measured ${at}` : "measurement time not recorded"})`;
}

/** "11 active · 11 from unindexed docs", from /status.eval_pairs. */
export function evalPairsText(status) {
  const pairs = status && status.eval_pairs;
  if (!pairs) return "—";
  if (!pairs.active) return "none";
  return pairs.not_indexed
    ? `${pairs.active} active · ${pairs.not_indexed} from unindexed docs`
    : `${pairs.active} active`;
}

/** Why the judge setting makes scores self-graded, or null when it does not. */
export function judgeWarning(judge) {
  if (!judge || judge.independent !== false) return null;
  const model = judge.answer_model || "the active chat model";
  if (judge.problem === "judge_same_as_answer_model") {
    return `This is the answer model (${model}), so it grades its own answers. Nightly runs will not start.`;
  }
  return `Not set, so ${model} grades its own answers. Nightly runs will not start.`;
}

/** "19 experiments · 0 promoted", health checks left out of both. */
export function experimentsSummary(status) {
  const experiments = asNumber(status && status.total_experiments) ?? 0;
  const promoted = asNumber(status && status.total_improvements) ?? 0;
  return `${experiments} experiment${experiments === 1 ? "" : "s"} · ${promoted} promoted`;
}
