import { describe, it, expect } from "vitest";
import { formatVideoStamp } from "./videoBatchStamp";

describe("formatVideoStamp", () => {
  const now = new Date("2026-10-10T20:00:00");

  it("carries the time of day, so same-prompt batches tell apart", () => {
    const a = formatVideoStamp("2026-10-10T19:42:00", now);
    const b = formatVideoStamp("2026-10-10T19:47:00", now);
    expect(a).not.toBe(b);
    expect(a).toMatch(/42/);
    expect(a).toMatch(/Oct/);
  });

  it("adds the year only when it is not this one", () => {
    expect(formatVideoStamp("2026-10-10T19:42:00", now)).not.toMatch(/2026/);
    expect(formatVideoStamp("2025-03-01T09:05:00", now)).toMatch(/2025/);
  });

  it("is empty when the batch has no time", () => {
    expect(formatVideoStamp(null, now)).toBe("");
    expect(formatVideoStamp("not a date", now)).toBe("");
  });
});
