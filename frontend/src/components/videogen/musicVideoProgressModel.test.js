import { describe, it, expect } from "vitest";
import {
  activeStepIndex,
  clipCellState,
  currentClipText,
  etaText,
  formatDuration,
  visibleStages,
} from "./musicVideoProgressModel";

describe("musicVideoProgressModel", () => {
  it("formats durations", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(750)).toBe("12m 30s");
    expect(formatDuration(3840)).toBe("1h 04m");
    expect(formatDuration(null)).toBe("");
    expect(formatDuration(-1)).toBe("");
  });

  it("labels the ETA basis", () => {
    expect(etaText({ eta_seconds: 750, eta_basis: "measured" })).toBe("~12m 30s left (measured)");
    expect(etaText({ eta_seconds: 60, eta_basis: "estimate" })).toBe("~1m 00s left (estimate)");
    expect(etaText({ eta_seconds: null })).toBe("");
  });

  it("maps clip cells", () => {
    expect(clipCellState({ index: 0, status: "done" }, 3)).toBe("done");
    expect(clipCellState({ index: 1, status: "done", review: { state: "needs_review" } })).toBe("held");
    expect(clipCellState({ index: 3, status: "pending" }, 3)).toBe("rendering");
    expect(clipCellState({ index: 4, status: "pending" }, 3)).toBe("pending");
    expect(clipCellState({ index: 5, status: "failed" })).toBe("failed");
  });

  it("describes the clip in flight", () => {
    const progress = {
      current_clip: { index: 5, position: 6, substage_label: "Animating (I2V)", step: 14, steps: 30 },
      stages: [{ key: "generating", detail: { clip_count: 12 } }],
    };
    expect(currentClipText(progress)).toBe("Clip 6/12 · Animating (I2V) 14/30");
    expect(currentClipText({ stages: [] })).toBe("");
  });

  it("hides skipped stages and finds the active step", () => {
    const stages = visibleStages({
      stages: [
        { key: "analyzing", state: "done" },
        { key: "awaiting_approval", state: "done" },
        { key: "storyboards", state: "skipped" },
        { key: "generating", state: "queued" },
        { key: "assembling", state: "pending" },
      ],
    });
    expect(stages.map((s) => s.key)).toEqual(["analyzing", "awaiting_approval", "generating", "assembling"]);
    expect(activeStepIndex(stages)).toBe(2);
    expect(activeStepIndex([{ state: "done" }, { state: "done" }])).toBe(2);
  });
});
