import { describe, it, expect } from "vitest";
import { formatUiError } from "./uiError";

describe("formatUiError", () => {
  it("passes strings through", () => {
    expect(formatUiError("boom")).toBe("boom");
  });

  it("returns empty string for null/undefined/empty", () => {
    expect(formatUiError(null)).toBe("");
    expect(formatUiError(undefined)).toBe("");
    expect(formatUiError("")).toBe("");
  });

  it("extracts message from the backend error envelope {code, message}", () => {
    expect(formatUiError({ code: "GENERIC_ERROR", message: "GPU busy" })).toBe("GPU busy");
  });

  it("unwraps a nested envelope {error: {code, message}}", () => {
    expect(
      formatUiError({ success: false, error: { code: "E1", message: "nested" } })
    ).toBe("nested");
  });

  it("extracts message from Error instances", () => {
    expect(formatUiError(new Error("kaput"))).toBe("kaput");
  });

  it("handles error/detail string fields", () => {
    expect(formatUiError({ error: "flat error" })).toBe("flat error");
    expect(formatUiError({ detail: "fastapi detail" })).toBe("fastapi detail");
  });

  it("stringifies unknown object shapes instead of returning them", () => {
    const out = formatUiError({ weird: true });
    expect(typeof out).toBe("string");
    expect(out).toContain("weird");
  });
});
