import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children, actions }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
vi.mock("../../components/notes/ClosedNotesDrawer", () => ({ default: () => null }));
vi.mock("../../contexts/LayoutContext", () => {
  const layout = {
    gridSettings: {
      CONTAINER_PADDING_PX: 4,
      CARD_MARGIN_PX: 8,
      COLS_COUNT: 175,
      ROW_HEIGHT_PX: 10,
      cardGridW: 35,
      cardGridH: 49,
      cardMinGridW: 30,
    },
  };
  return { useDashboardWidth: () => 1600, useLayout: () => layout };
});

import StickyNotesPage from "../StickyNotesPage";

let saved;
let posts;

beforeEach(() => {
  posts = [];
  global.ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
  saved = {
    notes: {
      a: { title: "Alpha", content: "first" },
      b: { title: "Beta", content: "second" },
      c: { title: "Gamma", content: "third" },
    },
    layout: [
      { i: "a", x: 0, y: 0, w: 35, h: 35 },
      { i: "b", x: 40, y: 0, w: 35, h: 35 },
      { i: "c", x: 80, y: 40, w: 35, h: 20 },
    ],
    noteColors: {},
    minimizedCards: {},
    pinnedNotes: {},
    closedNotes: {},
    layoutMode: "normal",
  };
  global.fetch = vi.fn(async (url, opts = {}) => {
    if ((opts.method || "GET") === "POST") {
      posts.push(JSON.parse(opts.body));
      return { ok: true, json: async () => ({}) };
    }
    return { ok: true, json: async () => saved };
  });
});

afterEach(() => {
  delete global.ResizeObserver;
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <StickyNotesPage />
    </MemoryRouter>,
  );

const pinGamma = async () => {
  fireEvent.contextMenu(await screen.findByText("Gamma"), { clientX: 10, clientY: 10 });
  fireEvent.click(screen.getByRole("menuitem", { name: "Pin to Top" }));
  await waitFor(() => expect(posts.at(-1)?.pinnedNotes).toEqual({ c: true }));
};

describe("Notes Pin to Top", () => {
  it("moves the pinned note to the top of the board and the others below it", async () => {
    renderPage();
    await pinGamma();

    const byId = Object.fromEntries(posts.at(-1).layout.map((item) => [item.i, item]));
    expect(byId.c).toMatchObject({ x: 0, y: 0 });
    expect(byId.a.y).toBeGreaterThanOrEqual(20);
    expect(byId.b.y).toBeGreaterThanOrEqual(20);
    expect(byId.b.x - byId.a.x).toBe(40);
  });

  it("keeps pinned notes first when the board switches to the compact layout", async () => {
    const { container } = renderPage();
    await pinGamma();

    fireEvent.click(screen.getByRole("button", { name: /^Layout:/ }));

    await waitFor(() => {
      const top = container.querySelector('[data-card-id="c"]').closest(".react-grid-item");
      const other = container.querySelector('[data-card-id="a"]').closest(".react-grid-item");
      expect(parseFloat(top.style.top)).toBeLessThanOrEqual(parseFloat(other.style.top));
      expect(parseFloat(top.style.left)).toBeLessThan(parseFloat(other.style.left));
    });
  });
});

describe("Notes Ctrl+Z", () => {
  it("leaves Ctrl+Z inside a note body to the editor", async () => {
    const { container } = renderPage();
    await pinGamma();
    const before = posts.length;

    const body = container.querySelector('[data-card-id="a"] .note-content');
    const notCancelled = fireEvent.keyDown(body, { key: "z", ctrlKey: true });

    expect(notCancelled).toBe(true);
    expect(posts.length).toBe(before);
  });

  it("still undoes board changes when focus is outside the notes", async () => {
    renderPage();
    await pinGamma();

    const notCancelled = fireEvent.keyDown(document.body, { key: "z", ctrlKey: true });

    expect(notCancelled).toBe(false);
    await waitFor(() => expect(posts.at(-1).pinnedNotes).toEqual({}));
  });
});
