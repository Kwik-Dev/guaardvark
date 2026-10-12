import { describe, it, expect } from "vitest";
import { isTerminalStatus, jobPill, resultLine, skippedPhrase } from "../importStatus";

describe("importStatus", () => {
  it("ends on either spelling of complete", () => {
    expect(isTerminalStatus("complete")).toBe(true);
    expect(isTerminalStatus("completed")).toBe(true);
    expect(isTerminalStatus("processing")).toBe(false);
  });

  it("never shows a finished job with failures as imported", () => {
    expect(jobPill({ status: "complete", stats: { errors: 2 } })).toEqual({ label: "Finished with errors (2 failed)", tone: "warn" });
    expect(jobPill({ status: "complete", stats: { errors: 0 } })).toEqual({ label: "Imported", tone: "ok" });
    expect(jobPill({ status: "complete", preview: true }).label).toBe("Preview ready");
  });

  it("words the skips the way the worker does", () => {
    expect(skippedPhrase({ hidden_dirs: 1, hidden_files: 1, sensitive_files: 1 }))
      .toBe("1 hidden or internal folder and 2 hidden or private files");
    expect(skippedPhrase({ hidden_dirs: 0, hidden_files: 0 })).toBe("");
  });

  it("puts the results in one line of plain counts", () => {
    expect(resultLine({ imported: 3, skipped: 4, errors: 0, queued_for_indexing: 3 }))
      .toBe("3 imported · 4 already there · 0 failed · 3 queued for indexing");
    expect(resultLine({ total_files: 1, skipped_private: { hidden_files: 2 } }, { preview: true }))
      .toBe("1 file would be imported · skipped 2 hidden or private files");
  });
});
