import { describe, expect, it } from "vitest";
import {
  isScanTerminal,
  resolveScanId,
  scanStatusFromPayload,
  scanUiPhase,
} from "../scanProgressState";

describe("resolveScanId", () => {
  it("prefers scan_id from the trigger payload", () => {
    expect(resolveScanId({ scan_id: 7, task_id: "abc" }, { id: 9 })).toBe(7);
  });

  it("falls back to the run id, then the celery task id", () => {
    expect(resolveScanId({ task_id: "abc" }, { id: 9 })).toBe(9);
    expect(resolveScanId({ task_id: "abc" }, null)).toBe("abc");
  });
});

describe("scanStatusFromPayload", () => {
  it("reads cancelled from the status route shapes", () => {
    expect(scanStatusFromPayload({ data: { status: "cancelled" } }, null)).toBe("cancelled");
    expect(
      scanStatusFromPayload({ data: { last_run: { id: 42, status: "cancelled" } } }, { id: 42, status: "running" }),
    ).toBe("cancelled");
    expect(scanStatusFromPayload({}, { status: "running" })).toBe("running");
  });

  it("does not treat a previous last_run as this scan", () => {
    expect(
      scanStatusFromPayload(
        { data: { last_run: { id: 1, status: "cancelled" } } },
        { id: 42, status: "running" },
      ),
    ).toBe("running");
  });
});

describe("scanUiPhase", () => {
  it("stays running until cancel is asked", () => {
    expect(scanUiPhase({ cancelRequested: false, runStatus: "running" })).toBe("running");
  });

  it("shows cancelling after Cancel until the status route reports a terminal state", () => {
    expect(scanUiPhase({ cancelRequested: true, runStatus: "running" })).toBe("cancelling");
  });

  it("turns cancelled when the status or socket says so, even if cancel was not clicked", () => {
    expect(scanUiPhase({ cancelRequested: true, runStatus: "cancelled" })).toBe("cancelled");
    expect(scanUiPhase({ cancelRequested: false, liveStage: "cancelled" })).toBe("cancelled");
  });

  it("turns completed if the scan finishes before cancel takes", () => {
    expect(scanUiPhase({ cancelRequested: true, runStatus: "success" })).toBe("completed");
    expect(scanUiPhase({ cancelRequested: true, liveStage: "complete" })).toBe("completed");
    expect(scanUiPhase({ cancelRequested: true, runStatus: "completed" })).toBe("completed");
  });

  it("treats cancelled, completed, failed and blocked as terminal", () => {
    expect(isScanTerminal("cancelling")).toBe(false);
    expect(isScanTerminal("running")).toBe(false);
    expect(isScanTerminal("cancelled")).toBe(true);
    expect(isScanTerminal("completed")).toBe(true);
    expect(isScanTerminal("failed")).toBe(true);
    expect(isScanTerminal("blocked")).toBe(true);
  });
});
