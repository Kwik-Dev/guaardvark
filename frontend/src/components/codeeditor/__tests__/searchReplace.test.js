import { describe, it, expect } from "vitest";
import { isEditableTab, replaceInTabs } from "../searchReplace";

const tab = (overrides) => ({ id: "t", filePath: "a.js", content: "", source: "local", ...overrides });

describe("replaceInTabs", () => {
  it("replaces every case-insensitive match in editable tabs and marks them modified", () => {
    const tabs = [tab({ content: "Foo foo FOO" }), tab({ id: "u", filePath: "b.js", content: "none" })];
    const result = replaceInTabs(tabs, "foo", "bar");
    expect(result.replaced).toBe(3);
    expect(result.files).toBe(1);
    expect(result.tabs[0].content).toBe("bar bar bar");
    expect(result.tabs[0].isModified).toBe(true);
    expect(result.tabs[1]).toBe(tabs[1]);
  });

  it("treats the search term literally and keeps $ in the replacement literal", () => {
    const result = replaceInTabs([tab({ content: "a.b a+b" })], "a.b", "$&x");
    expect(result.replaced).toBe(1);
    expect(result.tabs[0].content).toBe("$&x a+b");
  });

  it("leaves read-only and live repository tabs alone", () => {
    const tabs = [tab({ content: "foo", readOnly: true }), tab({ id: "r", content: "foo", source: "live_repo" })];
    const result = replaceInTabs(tabs, "foo", "bar");
    expect(result.replaced).toBe(0);
    expect(result.tabs[0]).toBe(tabs[0]);
    expect(isEditableTab(tabs[1])).toBe(false);
    expect(isEditableTab(tab({ content: "" }))).toBe(true);
  });

  it("does nothing without a term or tabs", () => {
    expect(replaceInTabs([tab({ content: "x" })], "", "y").replaced).toBe(0);
    expect(replaceInTabs(undefined, "x", "y")).toEqual({ tabs: [], replaced: 0, files: 0 });
  });
});
