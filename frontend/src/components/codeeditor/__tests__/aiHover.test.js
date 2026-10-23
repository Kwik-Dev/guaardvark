import { describe, it, expect, vi } from "vitest";
import { createAiHoverProvider } from "../aiHover";

const model = {
  getWordAtPosition: ({ column }) => (column > 0 ? { word: "fetch", startColumn: 1, endColumn: 6 } : null),
  getLineContent: () => "fetch(url)",
};
const position = { lineNumber: 3, column: 2 };

describe("createAiHoverProvider", () => {
  it("asks nothing while the toggle is off", async () => {
    const explain = vi.fn();
    const hover = createAiHoverProvider({ isEnabled: () => false, explain, getTab: () => ({ language: "javascript" }) });
    expect(await hover(model, position, {})).toBe(null);
    expect(explain).not.toHaveBeenCalled();
  });

  it("asks once per word on a line and serves repeats from the cache", async () => {
    const explain = vi.fn().mockResolvedValue({ success: true, analysis: "Performs a request." });
    const hover = createAiHoverProvider({ isEnabled: () => true, explain, getTab: () => ({ language: "javascript", filePath: "a.js" }) });
    const first = await hover(model, position, {});
    const second = await hover(model, position, {});
    expect(explain).toHaveBeenCalledTimes(1);
    expect(explain.mock.calls[0][0]).toMatchObject({ word: "fetch", language: "javascript", filePath: "a.js", content: "fetch(url)" });
    expect(first.contents[1].value).toBe("Performs a request.");
    expect(second).toEqual(first);
    expect(first.range).toEqual({ startLineNumber: 3, startColumn: 1, endLineNumber: 3, endColumn: 6 });
  });

  it("shares one in-flight request between overlapping hovers", async () => {
    let resolve;
    const explain = vi.fn(() => new Promise((r) => { resolve = r; }));
    const hover = createAiHoverProvider({ isEnabled: () => true, explain, getTab: () => ({}) });
    const a = hover(model, position, {});
    const b = hover(model, position, {});
    resolve({ success: true, analysis: "x" });
    expect((await a).contents[1].value).toBe("x");
    expect((await b).contents[1].value).toBe("x");
    expect(explain).toHaveBeenCalledTimes(1);
  });

  it("returns nothing for a cancelled hover, a failed answer or no word", async () => {
    const explain = vi.fn().mockResolvedValue({ success: false });
    const hover = createAiHoverProvider({ isEnabled: () => true, explain, getTab: () => ({}) });
    expect(await hover(model, position, {})).toBe(null);
    expect(await hover(model, { lineNumber: 3, column: 0 }, {})).toBe(null);
    const ok = vi.fn().mockResolvedValue({ success: true, analysis: "y" });
    const cancelled = createAiHoverProvider({ isEnabled: () => true, explain: ok, getTab: () => ({}) });
    expect(await cancelled(model, position, { isCancellationRequested: true })).toBe(null);
  });

  it("forgets a request that threw so the next hover can retry", async () => {
    const explain = vi.fn().mockRejectedValueOnce(new Error("down")).mockResolvedValueOnce({ success: true, analysis: "ok" });
    const cache = new Map();
    const hover = createAiHoverProvider({ isEnabled: () => true, explain, getTab: () => ({}), cache });
    expect(await hover(model, position, {})).toBe(null);
    expect(cache.size).toBe(0);
    expect((await hover(model, position, {})).contents[1].value).toBe("ok");
  });
});
