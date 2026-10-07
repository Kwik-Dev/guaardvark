import { describe, it, expect } from "vitest";
import {
  ensureLayoutItem,
  hiddenCardIds,
  showCard,
  visibleLayout,
  withHiddenItems,
} from "../dashboardCardVisibility";

const layout = [
  { i: "project", x: 0, y: 0, w: 10, h: 5 },
  { i: "tasks", x: 10, y: 0, w: 10, h: 5 },
  { i: "gpu", x: 20, y: 0, w: 10, h: 8 },
];

describe("dashboard hidden cards", () => {
  it("renders every card when none are hidden", () => {
    expect(visibleLayout(layout, {})).toBe(layout);
    expect(visibleLayout(layout, undefined)).toBe(layout);
  });

  it("filters hidden cards out of the rendered layout", () => {
    const hidden = { tasks: true, gpu: false };
    expect(hiddenCardIds(hidden)).toEqual(["tasks"]);
    expect(visibleLayout(layout, hidden).map((it) => it.i)).toEqual(["project", "gpu"]);
  });

  it("keeps a hidden card's place when the grid reports a drag without it", () => {
    const reported = [{ i: "project", x: 5, y: 0, w: 10, h: 5 }, layout[2]];
    const merged = withHiddenItems(reported, layout, { tasks: true });
    expect(merged.map((it) => it.i)).toEqual(["project", "gpu", "tasks"]);
    expect(merged.find((it) => it.i === "tasks")).toEqual(layout[1]);
  });

  it("shows one card or all of them", () => {
    expect(showCard({ tasks: true, gpu: true }, "tasks")).toEqual({ gpu: true });
    expect(showCard({ tasks: true, gpu: true })).toEqual({});
  });

  it("places a card that has no item in the current preset below the rest", () => {
    const preset = [layout[0], layout[2]];
    const out = ensureLayoutItem(preset, "tasks", { i: "tasks", x: 40, y: 0, w: 12, h: 6 });
    expect(out.find((it) => it.i === "tasks")).toMatchObject({ x: 0, y: 8, w: 12, h: 6 });
    expect(ensureLayoutItem(layout, "tasks", { i: "tasks", w: 1, h: 1 })).toBe(layout);
  });
});
