import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import MemoryManagementSection from "../MemoryManagementSection";

const memory = { id: 11, content: "Prefers metric units", type: "preference", status: "active", tags: [] };

// A real Response, so error statuses go through apiClient.handleResponse.
const json = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("Agent Memory failures", () => {
  let calls;
  let deleteStatus;
  beforeEach(() => {
    calls = [];
    deleteStatus = 500;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url, opts = {}) => {
        const method = opts.method || "GET";
        calls.push({ url: String(url), method });
        if (method === "DELETE") {
          return deleteStatus === 200
            ? json(200, { success: true })
            : json(deleteStatus, { success: false, error: "Memory store is read-only" });
        }
        return json(200, { success: true, memories: [memory], total: 1 });
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("asks with the confirm dialog, then says why a delete failed", async () => {
    render(<MemoryManagementSection />);
    await screen.findByText("Prefers metric units");

    fireEvent.contextMenu(screen.getByText("Prefers metric units"), { clientX: 20, clientY: 20 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Delete this memory");
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    fireEvent.click(within(dialog).getByRole("button", { name: "Delete memory" }));
    expect(await screen.findByText("Memory store is read-only")).toBeInTheDocument();
    expect(screen.getByText("Prefers metric units")).toBeInTheDocument();
  });

  it("shows a failed load as an error with Retry, not 'No memories found.'", async () => {
    let first = true;
    fetch.mockImplementation(async () => {
      if (first) {
        first = false;
        return json(503, { error: "Database is not reachable" });
      }
      return json(200, { success: true, memories: [memory], total: 1 });
    });
    render(<MemoryManagementSection />);

    expect(await screen.findByText("Database is not reachable")).toBeInTheDocument();
    expect(screen.queryByText("No memories found.")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByText("Prefers metric units")).toBeInTheDocument());
  });
});
