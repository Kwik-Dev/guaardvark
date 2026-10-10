import { describe, it, expect } from "vitest";
import { saveTargetFor, TAB_SOURCE } from "../tabOrigin";

describe("saveTargetFor", () => {
  it("saves a library document by its id, never by its name", () => {
    const target = saveTargetFor({ source: TAB_SOURCE.DOCUMENT, documentId: 42, filePath: "app.py" });
    expect(target).toEqual({ kind: "document", documentId: 42 });
    expect(target.path).toBeUndefined();
  });

  it("refuses a document tab that lost its id instead of guessing a path", () => {
    const target = saveTargetFor({ source: TAB_SOURCE.DOCUMENT, documentId: null, filePath: "app.py" });
    expect(target.kind).toBe("error");
    expect(target.message).toMatch(/no id/);
  });

  it("saves a repo file by its repo-relative path", () => {
    expect(saveTargetFor({ source: TAB_SOURCE.FILESYSTEM, filePath: "backend/app.py" })).toEqual({
      kind: "repo",
      path: "backend/app.py",
    });
    expect(saveTargetFor({ source: TAB_SOURCE.FILESYSTEM, filePath: " " }).kind).toBe("error");
  });

  it("keeps live repo views and read-only tabs out of every write path", () => {
    expect(saveTargetFor({ source: TAB_SOURCE.LIVE_REPO, filePath: "backend/app.py" }).kind).toBe("readonly");
    expect(saveTargetFor({ source: TAB_SOURCE.DOCUMENT, documentId: 3, readOnly: true }).kind).toBe("readonly");
  });

  it("sends new and local tabs to browser storage", () => {
    expect(saveTargetFor({ isNew: true, filePath: null })).toEqual({ kind: "local", path: null });
    expect(saveTargetFor({ source: TAB_SOURCE.LOCAL, filePath: "scratch.js" })).toEqual({ kind: "local", path: "scratch.js" });
    expect(saveTargetFor(null)).toEqual({ kind: "none" });
  });
});
