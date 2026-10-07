import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import MemoryManagementSection from "../MemoryManagementSection";

const memory = { id: 11, content: "Prefers metric units", type: "preference", status: "active", tags: [] };

describe("Agent Memory row menu", () => {
  let calls;
  beforeEach(() => {
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url, opts = {}) => {
        calls.push({ url: String(url), method: opts.method || "GET", body: opts.body });
        if ((opts.method || "GET") === "PATCH") {
          const { status } = JSON.parse(opts.body);
          return { json: async () => ({ success: true, memory: { ...memory, status } }) };
        }
        return { json: async () => ({ success: true, memories: [memory] }) };
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("re-exposes the row's status actions with the buttons' conditions", async () => {
    render(<MemoryManagementSection />);
    const cell = await screen.findByText("Prefers metric units");
    fireEvent.contextMenu(cell, { clientX: 20, clientY: 20 });

    const labels = screen.getAllByRole("menuitem").map((el) => el.textContent);
    expect(labels).toEqual(["Edit…", "Copy content", "Archive", "Mark wrong", "Delete"]);

    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch.url).toMatch(/\/memory\/11$/);
    expect(JSON.parse(patch.body)).toEqual({ status: "archived" });

    fireEvent.contextMenu(screen.getByText("Prefers metric units"), { clientX: 20, clientY: 20 });
    expect(screen.getByRole("menuitem", { name: "Restore" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Archive" })).toHaveAttribute("aria-disabled", "true");
  });
});
