import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import MemoryManagementSection from "../MemoryManagementSection";

const saved = {
  id: "m-1",
  content: "Prefers metric units",
  type: "preference",
  status: "active",
  tags: [],
  created_at: "2026-10-12T14:03:22",
};

describe("Agent Memory add when the text is already saved", () => {
  let calls;
  beforeEach(() => {
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url, opts = {}) => {
        calls.push({ url: String(url), method: opts.method || "GET", body: opts.body });
        if ((opts.method || "GET") === "POST") {
          return { json: async () => ({ success: true, already_saved: true, memory: saved }) };
        }
        return { json: async () => ({ success: true, memories: [saved], total: 1 }) };
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("says Already saved, names the saved memory and closes the dialog", async () => {
    render(<MemoryManagementSection />);
    await screen.findByText("Prefers metric units");

    fireEvent.click(screen.getByRole("button", { name: /Add Memory/ }));
    fireEvent.change(screen.getByLabelText("Memory Content"), { target: { value: "prefers metric units." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const notice = await screen.findByText(/^Already saved:/);
    expect(notice.textContent).toBe(
      'Already saved: "Prefers metric units" is a preference from 10/12 14:03:22. Nothing new was added.',
    );
    await waitFor(() => expect(screen.queryByLabelText("Memory Content")).toBeNull());
    expect(JSON.parse(calls.find((c) => c.method === "POST").body).content).toBe("prefers metric units.");
  });
});
