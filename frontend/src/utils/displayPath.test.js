import { describe, expect, it } from "vitest";
import { displayPath, rootLabel } from "./displayPath";

describe("displayPath", () => {
  it("writes the home folder as ~ and leaves other paths alone", () => {
    expect(displayPath("/home/u", "/home/u")).toBe("~");
    expect(displayPath("/home/u/app/data/uploads", "/home/u/")).toBe("~/app/data/uploads");
    expect(displayPath("/home/user2/x", "/home/u")).toBe("/home/user2/x");
    expect(displayPath("/media", "/home/u")).toBe("/media");
    expect(displayPath("/media", null)).toBe("/media");
  });

  it("labels the home root Home", () => {
    expect(rootLabel("/home/u", "/home/u")).toBe("Home");
    expect(rootLabel("/home/u/app/data/uploads", "/home/u")).toBe("~/app/data/uploads");
    expect(rootLabel("/media", "/home/u")).toBe("/media");
  });
});
