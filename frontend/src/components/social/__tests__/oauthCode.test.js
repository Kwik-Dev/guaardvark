import { describe, expect, it } from "vitest";

import { codeFromPaste } from "../oauthCode";

describe("codeFromPaste", () => {
  it("takes a bare code, without a trailing #_", () => {
    expect(codeFromPaste("  abc123#_ ")).toEqual({ code: "abc123", error: null });
    expect(codeFromPaste("")).toEqual({ code: "", error: null });
  });

  it("reads the code from the address the browser landed on", () => {
    const address = "http://localhost:5173/connections?state=s1&code=xyz#_";
    expect(codeFromPaste(address, "s1")).toEqual({ code: "xyz", error: null });
  });

  it("refuses an address from another authorization attempt", () => {
    const result = codeFromPaste("http://localhost:5173/connections?state=other&code=xyz", "s1");
    expect(result.code).toBe("");
    expect(result.error).toMatch(/different authorization attempt/);
  });

  it("reports a refusal and an address without a code", () => {
    expect(codeFromPaste("http://localhost/connections?error=access_denied&state=s1", "s1").error).toMatch(
      /not granted: access_denied/,
    );
    expect(codeFromPaste("http://localhost/connections?state=s1", "s1").error).toMatch(/no authorization code/);
  });
});
