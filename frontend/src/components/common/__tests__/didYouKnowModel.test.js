import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  SEEN_TIPS_KEY,
  markTipSeen,
  markTipShownThisSession,
  pickTip,
  readSeenTips,
  resetTipSessionForTests,
  tipShownThisSession,
} from "../didYouKnowModel";

const TIPS = [
  { id: "a", text: "A" },
  { id: "b", text: "B", route: "/notes" },
  { id: "c", text: "C", route: "/dashboard" },
];

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  resetTipSessionForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("pickTip", () => {
  it("starts with the first tip when nothing has been seen", () => {
    expect(pickTip(TIPS, []).id).toBe("a");
  });

  it("prefers a tip that has not been seen", () => {
    expect(pickTip(TIPS, ["a"]).id).toBe("b");
    expect(pickTip(TIPS, ["a", "b"]).id).toBe("c");
  });

  it("comes round to the tip seen longest ago once every tip has been seen", () => {
    expect(pickTip(TIPS, ["b", "c", "a"]).id).toBe("b");
    expect(pickTip(TIPS, ["c", "a", "b"]).id).toBe("c");
  });

  it("never repeats the tip on screen", () => {
    expect(pickTip(TIPS, ["a", "b", "c"], { excludeId: "a" }).id).toBe("b");
    expect(pickTip([{ id: "only", text: "x" }], ["only"], { excludeId: "only" })).toBeNull();
  });

  it("skips tips about pages the profile hides", () => {
    expect(pickTip(TIPS, ["a"], { hiddenRoutes: ["/notes"] }).id).toBe("c");
    expect(pickTip(TIPS, ["a", "b"], { hiddenRoutes: ["/notes", "/"] }).id).toBe("a");
  });

  it("returns null when there is nothing to show", () => {
    expect(pickTip([], [])).toBeNull();
    expect(pickTip(undefined, [])).toBeNull();
  });
});

describe("seen tips", () => {
  it("remembers what was shown, most recent last", () => {
    markTipSeen("a");
    markTipSeen("b");
    markTipSeen("a");
    expect(readSeenTips()).toEqual(["b", "a"]);
    expect(JSON.parse(window.localStorage.getItem(SEEN_TIPS_KEY))).toEqual(["b", "a"]);
  });

  it("reads a damaged entry as nothing seen", () => {
    window.localStorage.setItem(SEEN_TIPS_KEY, "{not json");
    expect(readSeenTips()).toEqual([]);
    window.localStorage.setItem(SEEN_TIPS_KEY, JSON.stringify({ a: 1 }));
    expect(readSeenTips()).toEqual([]);
  });

  it("does not throw when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readSeenTips()).toEqual([]);
    expect(() => markTipSeen("a")).not.toThrow();
    expect(() => markTipShownThisSession()).not.toThrow();
    expect(tipShownThisSession()).toBe(true);
  });
});

describe("one tip per session", () => {
  it("is unset until a tip is shown", () => {
    expect(tipShownThisSession()).toBe(false);
    markTipShownThisSession();
    expect(tipShownThisSession()).toBe(true);
  });
});
