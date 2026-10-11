import { describe, it, expect } from "vitest";
import {
  saveTargetFor,
  saveRefusal,
  isDirty,
  loadingTab,
  loadedTab,
  failedTab,
  TAB_SOURCE,
  TAB_LOAD,
} from "../tabOrigin";

const docTab = { id: "t1", source: TAB_SOURCE.DOCUMENT, documentId: 42, filePath: "app.py", language: "python" };

describe("tab load states", () => {
  it("keeps placeholder text out of a loading tab and refuses to save it", () => {
    const tab = loadingTab(docTab);
    expect(tab.content).toBe("");
    expect(tab.loadState).toBe(TAB_LOAD.LOADING);
    expect(saveRefusal(tab)).toMatchObject({ reason: "loading" });
  });

  it("keeps error text out of a failed tab and refuses to save it", () => {
    for (const error of [new Error("Document not found"), "Failed to fetch document content (500)", undefined]) {
      const tab = failedTab(docTab, error);
      expect(tab.content).toBe("");
      expect(tab.loadState).toBe(TAB_LOAD.FAILED);
      expect(saveRefusal(tab)).toMatchObject({ reason: "failed" });
    }
    expect(failedTab(docTab, new Error("Document not found")).loadError).toBe("Document not found");
    expect(saveRefusal(failedTab(docTab, "boom")).message).toMatch(/did not load \(boom\)/);
  });

  it("refuses to save a loaded tab whose content is what was loaded, including an empty document", () => {
    expect(saveRefusal(loadedTab(docTab, "x = 1\n"))).toMatchObject({ reason: "clean" });
    const empty = loadedTab(docTab, "");
    expect(empty.content).toBe("");
    expect(saveRefusal(empty)).toMatchObject({ reason: "clean" });
    expect(saveRefusal(loadedTab(docTab, undefined))).toMatchObject({ reason: "clean" });
  });

  it("allows a save once the loaded content changed, and not after it changes back", () => {
    const tab = loadedTab(docTab, "x = 1\n");
    expect(isDirty({ ...tab, content: "x = 2\n" })).toBe(true);
    expect(saveRefusal({ ...tab, content: "x = 2\n" })).toBe(null);
    // Typed, then typed back: the modified flag is set but nothing differs from the baseline.
    expect(saveRefusal({ ...tab, content: "x = 1\n", isModified: true })).toMatchObject({ reason: "clean" });
  });

  it("falls back to the modified flag for tabs opened without a baseline", () => {
    expect(saveRefusal({ ...docTab, content: "x", isModified: false })).toMatchObject({ reason: "clean" });
    expect(saveRefusal({ ...docTab, content: "x", isModified: true })).toBe(null);
  });

  it("treats a new tab as clean until something is typed", () => {
    const fresh = loadedTab({ id: "n", isNew: true, filePath: null }, "");
    expect(saveRefusal(fresh)).toMatchObject({ reason: "clean" });
    expect(saveRefusal({ ...fresh, content: "hello" })).toBe(null);
  });
});

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
