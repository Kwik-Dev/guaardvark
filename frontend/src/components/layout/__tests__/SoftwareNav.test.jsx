import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import SoftwareNav from "../SoftwareNav";
import { VoiceSessionProvider } from "../../../contexts/VoiceSessionContext";

// This test describes the core catalog; a distribution's brand.jsx may
// carry another one, so the brand is pinned to the core lists here.
vi.mock("../../../config/brand", async (importOriginal) => {
  const actual = await importOriginal();
  const { CORE_NAV_CATALOG, WORKSPACES } = await import("../../../config/navCatalog");
  const alerts = {
    id: "alerts", kind: "page", path: "/documents/alerts", label: "Alerts",
    icon: React.createElement("span", null, "!"), pinned: true, listed: false, badge: "alerts",
  };
  const brand = {
    ...actual.default,
    navCatalog: [...CORE_NAV_CATALOG, alerts],
    workspaces: WORKSPACES,
  };
  return { ...actual, brand, default: brand };
});

vi.mock("../../../config/navBadges", () => ({
  useNavBadgeCounts: () => ({ alerts: 4 }),
}));

vi.mock("../../../hooks/usePendingApprovals", () => ({
  usePendingApprovals: () => ({ count: 0 }),
}));

vi.mock("../../modals/SystemMetricsModal", () => ({
  default: () => null,
}));

vi.mock("../../agent/AgentScreenViewer", () => ({
  default: () => null,
}));

vi.mock("../../../contexts/VoiceContext", () => ({
  useVoice: () => ({ isPlaying: false, availableVoices: [] }),
}));

function renderNav(path = "/batch-images") {
  return render(
    <ThemeProvider theme={createTheme()}>
      <MemoryRouter initialEntries={[path]}>
        <SoftwareNav />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("SoftwareNav", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows workspaces and the studio tool strip on a studio route", () => {
    renderNav("/batch-images");
    expect(screen.getByRole("navigation", { name: "Workspace" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Studio" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("tablist", { name: "Workspace tools" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Image Gen/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /Video Gen/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Film Crew/ })).toBeInTheDocument();
  });

  it("hides the tool strip when the workspace has a single page", () => {
    renderNav("/code-editor");
    expect(screen.getByRole("button", { name: "Code" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("tablist", { name: "Workspace tools" })).not.toBeInTheDocument();
  });

  it("pins the catalog's actions and Settings on the right", () => {
    renderNav("/chat");
    expect(screen.getByRole("button", { name: "System Metrics" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Agent Screen" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  });

  it("ends the right side with a floating-chat button, off on a page that is a chat itself", () => {
    const { unmount } = renderNav("/documents");
    const button = screen.getByRole("button", { name: "Floating chat" });
    expect(button).toBeEnabled();
    expect(button.parentElement.nextElementSibling).toBeNull();
    unmount();
    renderNav("/chat");
    expect(screen.getByRole("button", { name: "Floating chat" })).toBeDisabled();
  });

  it("pins a brand page with its live badge count", () => {
    renderNav("/documents/alerts");
    const pin = screen.getByRole("link", { name: "Alerts" });
    expect(pin).toHaveAttribute("href", "/documents/alerts");
    expect(pin).toHaveTextContent("4");
    expect(screen.getByRole("button", { name: "Library" })).toHaveAttribute("aria-current", "page");
  });

  it("lights only the child tab on a nested page", () => {
    renderNav("/agents/memory");
    expect(screen.getByRole("tab", { name: /Agent Memory/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /^Agents$/ })).toHaveAttribute("aria-selected", "false");
  });

  it("lights only Bulk Import on the bulk import page", () => {
    renderNav("/documents/bulk-import");
    expect(screen.getByRole("tab", { name: /Bulk Import/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /^Files$/ })).toHaveAttribute("aria-selected", "false");
  });

  it("keeps the parent tab lit on a sub-route with no tab of its own", () => {
    renderNav("/agents/mcp");
    expect(screen.getByRole("tab", { name: /^Agents$/ })).toHaveAttribute("aria-selected", "true");
  });

  it("surfaces pages the sidebar does not list", () => {
    renderNav("/chat");
    expect(screen.getByRole("tab", { name: /^Voice$/ })).toBeInTheDocument();
  });

  it("puts the global mic immediately left of the floating-chat button", () => {
    render(
      <ThemeProvider theme={createTheme()}>
        <MemoryRouter initialEntries={["/documents"]}>
          <VoiceSessionProvider>
            <SoftwareNav />
          </VoiceSessionProvider>
        </MemoryRouter>
      </ThemeProvider>,
    );
    const mic = screen.getByRole("button", { name: /^Voice: Mic off/ });
    const chat = screen.getByRole("button", { name: "Floating chat" });
    expect(mic.parentElement.nextElementSibling.contains(chat)).toBe(true);
  });
});
