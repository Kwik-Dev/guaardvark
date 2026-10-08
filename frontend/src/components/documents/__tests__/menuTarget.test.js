import { describe, it, expect } from "vitest";
import { menuTargetPath, parentPath, folderLabel } from "../menuTarget";

describe("menuTargetPath", () => {
  it("is the desktop for a right-click on empty desktop space", () => {
    expect(menuTargetPath("desktop", null)).toBe("/");
  });

  it("is the folder a window is showing, including a subfolder it navigated into", () => {
    expect(menuTargetPath("folder-window", { id: 4, path: "Reports/Q1" })).toBe("Reports/Q1");
  });

  it("is the right-clicked folder itself", () => {
    expect(menuTargetPath("folder", { id: 9, path: "Archive" })).toBe("Archive");
  });

  it("is the folder holding a right-clicked file", () => {
    expect(menuTargetPath("file", { id: 1, path: "Reports/Q1/jan.txt" })).toBe("Reports/Q1");
    expect(menuTargetPath("file", { id: 2, path: "notes.txt" })).toBe("/");
  });

  it("never reads a click event as a target", () => {
    expect(menuTargetPath("click", { type: "click" })).toBe("/");
  });
});

describe("parentPath / folderLabel", () => {
  it("handles top-level and nested paths", () => {
    expect(parentPath("a")).toBe("/");
    expect(parentPath("a/b/")).toBe("a");
    expect(parentPath("")).toBe("/");
    expect(folderLabel("/")).toBe("Files");
    expect(folderLabel("Reports/Q1")).toBe("Q1");
  });
});
