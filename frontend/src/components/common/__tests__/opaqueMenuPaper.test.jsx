import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@mui/material/styles";

vi.mock("../../layout/PageLayout", () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock("../../notes/ClosedNotesDrawer", () => ({ default: () => null }));
vi.mock("../../../contexts/LayoutContext", () => {
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

import { themes } from "../../../theme/themes";
import { opaqueSurface } from "../opaqueMenuPaper";
import StickyNotesPage from "../../../pages/StickyNotesPage";

beforeEach(() => {
  global.ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
  const saved = {
    notes: { a: { title: "Shopping", content: "milk" } },
    layout: [{ i: "a", x: 0, y: 0, w: 35, h: 35 }],
    noteColors: { a: "#fff59d" },
    minimizedCards: {},
    pinnedNotes: {},
    closedNotes: {},
    layoutMode: "normal",
  };
  global.fetch = vi.fn(async () => ({ ok: true, json: async () => saved }));
});

afterEach(() => {
  delete global.ResizeObserver;
});

describe("opaque menu surface", () => {
  it("uses a solid theme colour in every built-in theme", () => {
    expect(opaqueSurface(themes.guaardvark.theme)).toBe("#080a0e");
    expect(opaqueSurface(themes.default.theme)).toBe("#1e1e1e");
    expect(opaqueSurface(themes.vader.theme)).toBe("#000000");
    expect(opaqueSurface(themes.light.theme)).toBe("#ffffff");
    for (const { theme } of Object.values(themes)) {
      expect(opaqueSurface(theme)).not.toMatch(/rgba|hsla/);
    }
  });

  it("gives the Notes right-click menu a solid background under the glass Guaardvark theme", async () => {
    render(
      <ThemeProvider theme={themes.guaardvark.theme}>
        <MemoryRouter>
          <StickyNotesPage />
        </MemoryRouter>
      </ThemeProvider>,
    );
    fireEvent.contextMenu(await screen.findByText("Shopping"), { clientX: 10, clientY: 10 });
    const paper = (await screen.findByRole("menu")).closest(".MuiPaper-root");
    const style = getComputedStyle(paper);
    expect(style.backgroundColor).toBe("rgb(8, 10, 14)");
    expect(style.backdropFilter || "none").toBe("none");
  });
});
