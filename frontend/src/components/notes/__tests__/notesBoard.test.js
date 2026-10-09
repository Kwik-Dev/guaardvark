import { describe, it, expect } from "vitest";
import { pinnedToTop, pinnedFirst, isEditableTarget } from "../notesBoard";

const COLS = 100;

describe("pinnedToTop", () => {
  it("moves a newly pinned note to the top-left and the rest below it", () => {
    const layout = [
      { i: "a", x: 0, y: 0, w: 30, h: 20 },
      { i: "b", x: 30, y: 0, w: 30, h: 20 },
      { i: "c", x: 0, y: 25, w: 30, h: 10 },
    ];

    const next = pinnedToTop(layout, { c: true }, COLS);
    const byId = Object.fromEntries(next.map((item) => [item.i, item]));

    expect(byId.c).toMatchObject({ x: 0, y: 0 });
    expect(byId.a.y).toBeGreaterThanOrEqual(byId.c.y + byId.c.h);
    expect(byId.b.y).toBeGreaterThanOrEqual(byId.c.y + byId.c.h);
    // The unpinned notes keep their arrangement relative to each other.
    expect(byId.b.y - byId.a.y).toBe(0);
    expect(byId.b.x - byId.a.x).toBe(30);
  });

  it("packs several pinned notes side by side in reading order and wraps at the edge", () => {
    const layout = [
      { i: "p2", x: 50, y: 40, w: 40, h: 10 },
      { i: "p1", x: 10, y: 40, w: 40, h: 15 },
      { i: "p3", x: 0, y: 60, w: 40, h: 5 },
      { i: "u", x: 0, y: 0, w: 20, h: 10 },
    ];

    const next = pinnedToTop(layout, { p1: true, p2: true, p3: true }, COLS);
    const byId = Object.fromEntries(next.map((item) => [item.i, item]));

    expect(byId.p1).toMatchObject({ x: 0, y: 0 });
    expect(byId.p2).toMatchObject({ x: 40, y: 0 });
    expect(byId.p3).toMatchObject({ x: 0, y: 15 });
    expect(byId.u.y).toBe(20);
    expect(next.map((item) => item.i)).toEqual(["p2", "p1", "p3", "u"]);
  });

  it("leaves a layout that already has the pinned band on top unchanged", () => {
    const layout = [
      { i: "p", x: 0, y: 0, w: 30, h: 20 },
      { i: "u", x: 5, y: 30, w: 30, h: 20 },
    ];

    expect(pinnedToTop(layout, { p: true }, COLS)).toEqual(layout);
  });

  it("uses the height a note occupies, not its stored height", () => {
    const layout = [
      { i: "p", x: 40, y: 50, w: 30, h: 40 },
      { i: "u", x: 0, y: 0, w: 30, h: 20 },
    ];

    const next = pinnedToTop(layout, { p: true }, COLS, (item) => (item.i === "p" ? 4 : item.h));

    expect(next.find((item) => item.i === "u").y).toBe(4);
  });

  it("returns the layout untouched when nothing is pinned", () => {
    const layout = [{ i: "a", x: 3, y: 7, w: 10, h: 10 }];
    expect(pinnedToTop(layout, {}, COLS)).toBe(layout);
  });
});

describe("pinnedFirst", () => {
  it("puts pinned ids first and keeps each group's order", () => {
    expect(pinnedFirst(["a", "b", "c", "d"], { c: true, a: true })).toEqual(["a", "c", "b", "d"]);
  });
});

describe("isEditableTarget", () => {
  it("is true inside a contenteditable note body, an input or a textarea", () => {
    const body = document.createElement("div");
    body.setAttribute("contenteditable", "true");
    const inner = document.createElement("b");
    body.appendChild(inner);
    document.body.appendChild(body);

    expect(isEditableTarget(body)).toBe(true);
    expect(isEditableTarget(inner)).toBe(true);
    expect(isEditableTarget(document.createElement("input"))).toBe(true);
    expect(isEditableTarget(document.createElement("textarea"))).toBe(true);
    body.remove();
  });

  it("is false for the page itself and for contenteditable=false", () => {
    const off = document.createElement("div");
    off.setAttribute("contenteditable", "false");
    document.body.appendChild(off);

    expect(isEditableTarget(document.body)).toBe(false);
    expect(isEditableTarget(off)).toBe(false);
    expect(isEditableTarget(window)).toBe(false);
    off.remove();
  });
});
