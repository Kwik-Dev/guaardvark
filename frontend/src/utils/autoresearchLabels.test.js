import { describe, it, expect } from "vitest";

import {
  baselineText,
  describeReasons,
  evalPairsText,
  experimentLabel,
  experimentsSummary,
  formatDelta,
  judgeWarning,
  reasonCode,
  runOutcome,
} from "./autoresearchLabels";

const NOT_INDEXED =
  "eval_sources_not_indexed — 11 of 11 active eval pairs come from documents " +
  "that are not indexed (PENDING: 11) — index them or regenerate";

describe("runOutcome", () => {
  it("says why a refused run did not run", () => {
    const out = runOutcome({ status: "failed_precondition", halt_reason: NOT_INDEXED });
    expect(out.kind).toBe("not_run");
    expect(out.text).toBe(
      "Not run: the eval questions come from documents that are not indexed",
    );
    expect(out.detail).toBe(NOT_INDEXED);
  });

  it("lists every reason a run refused on", () => {
    const out = runOutcome({
      status: "failed_precondition",
      halt_reason: "ollama_unreachable (ConnectionError) — start Ollama; judge_unset — x",
    });
    expect(out.text).toBe("Not run: Ollama is not running; no separate judge model is set");
  });

  it("shows baseline to latest with the best tried, even when latest is lower", () => {
    const out = runOutcome({
      status: "completed",
      baseline_score: 2.394,
      latest_score: 2.094,
      best_tried_score: 2.494,
      halt_reason: "plateaued",
    });
    expect(out.kind).toBe("done");
    expect(out.text).toBe("2.394 → 2.094 (best 2.494)");
    expect(out.detail).toBe("Halt: plateaued");
  });

  it("does not stand the baseline in for a missing result", () => {
    const out = runOutcome({ status: "completed", baseline_score: 4.94, best_score: 4.94 });
    expect(out.text).toBe("4.940 → —");
  });

  it("handles a run that has not measured anything yet", () => {
    expect(runOutcome({ status: "pending" }).text).toBe("Starting…");
    expect(runOutcome({ status: "running" }).kind).toBe("active");
    expect(runOutcome(null).text).toBe("—");
  });
});

describe("experimentLabel", () => {
  it("labels pytest snapshots as health checks, old and new statuses alike", () => {
    expect(experimentLabel({ proposal_source: "heal", status: "keep" }).status).toBe(
      "health check: pass",
    );
    expect(experimentLabel({ proposal_source: "heal", status: "discard" }).status).toBe(
      "health check: fail",
    );
    const skipped = experimentLabel({ proposal_source: "heal", status: "skipped" });
    expect(skipped.status).toBe("health check: skipped");
    expect(skipped.kind).toBe("health");
  });

  it("marks code-arm and posted rows as self-reported", () => {
    const arm = experimentLabel({ proposal_source: "code_arm", status: "keep" });
    expect(arm.selfReported).toBe(true);
    expect(arm.source).toBe("self-reported");
    const posted = experimentLabel({
      proposal_source: "tpe",
      status: "discard",
      retrieval_metrics: { self_reported: true },
    });
    expect(posted.selfReported).toBe(true);
  });

  it("passes parameter experiments through with a tone for the status", () => {
    const keep = experimentLabel({ proposal_source: "tpe", status: "keep" });
    expect(keep).toMatchObject({ kind: "experiment", status: "keep", source: "tpe", tone: "success" });
    expect(experimentLabel({ proposal_source: "llm", status: "crash" }).tone).toBe("error");
  });
});

describe("status lines", () => {
  it("shows the baseline with when it was measured, or that it was not", () => {
    expect(baselineText({ baseline_score: 0 })).toBe("Baseline: not measured");
    expect(baselineText({ baseline_score: 2.3938 })).toBe(
      "Baseline: 2.394 (measurement time not recorded)",
    );
    expect(
      baselineText({ baseline_score: 2.3938, baseline_measured_at: "2026-10-07T12:00:00" }),
    ).toMatch(/^Baseline: 2\.394 \(measured 2026-10-07 \d\d:\d\d\)$/);
  });

  it("gives one eval-pair count with the unindexed share", () => {
    const status = { eval_pairs: { active: 11, not_indexed: 11, by_status: { PENDING: 11 } } };
    expect(evalPairsText(status)).toBe("11 active · 11 from unindexed docs");
    expect(evalPairsText({ eval_pairs: { active: 20, not_indexed: 0 } })).toBe("20 active");
    expect(evalPairsText({ eval_pairs: { active: 0, not_indexed: 0 } })).toBe("none");
    expect(evalPairsText({})).toBe("—");
  });

  it("warns only when the judge grades its own model's answers", () => {
    expect(
      judgeWarning({ independent: false, problem: "judge_unset", answer_model: "gemma4:12b" }),
    ).toBe("Not set, so gemma4:12b grades its own answers. Nightly runs will not start.");
    expect(
      judgeWarning({
        independent: false,
        problem: "judge_same_as_answer_model",
        answer_model: "gemma4:12b",
      }),
    ).toMatch(/^This is the answer model \(gemma4:12b\)/);
    expect(judgeWarning({ independent: true })).toBeNull();
    expect(judgeWarning(null)).toBeNull();
  });

  it("counts experiments and promotions", () => {
    expect(experimentsSummary({ total_experiments: 19, total_improvements: 0 })).toBe(
      "19 experiments · 0 promoted",
    );
    expect(experimentsSummary({ total_experiments: 1, total_improvements: 1 })).toBe(
      "1 experiment · 1 promoted",
    );
  });
});

describe("helpers", () => {
  it("reads the reason code and signs deltas", () => {
    expect(reasonCode(NOT_INDEXED)).toBe("eval_sources_not_indexed");
    expect(describeReasons("")).toBe("no reason recorded");
    expect(describeReasons("something_new happened")).toBe("something_new happened");
    expect(formatDelta(2.094, 2.394)).toBe("-0.300");
    expect(formatDelta(2.494, 2.394)).toBe("+0.100");
    expect(formatDelta(null, 2.394)).toBe("");
  });
});
