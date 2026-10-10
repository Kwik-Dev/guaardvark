import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import MemoryManagementSection from "../MemoryManagementSection";

// 230 rows on the server, every status; the API serves one page per call and
// the real count in total.
const TOTAL = 230;
const row = (i) => ({
  id: `m-${i}`,
  content: `fact ${i}`,
  type: "fact",
  status: i % 10 === 0 ? "wrong" : "active",
  tags: [],
});

const page = (url) => {
  const params = new URL(url).searchParams;
  const limit = Number(params.get("limit"));
  const offset = Number(params.get("offset") || 0);
  const memories = [];
  for (let i = offset + 1; i <= Math.min(TOTAL, offset + limit); i += 1) memories.push(row(i));
  return { success: true, memories, total: TOTAL, limit, offset };
};

describe("Agent Memory list past the first page", () => {
  let calls;
  beforeEach(() => {
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url, opts = {}) => {
        calls.push({ url: String(url), method: opts.method || "GET" });
        return { json: async () => page(String(url)) };
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows the API's total, loads 100 at a time and offers Load more while rows remain", async () => {
    render(<MemoryManagementSection />);
    await screen.findByText("fact 1");
    expect(screen.getByText(/Total: 230/)).toBeInTheDocument();
    expect(screen.getByText(/\(showing 100\)/)).toBeInTheDocument();
    const first = new URL(calls[0].url);
    expect(first.searchParams.get("limit")).toBe("100");
    expect(first.searchParams.get("status")).toBe("active");

    fireEvent.click(screen.getByRole("button", { name: /Load more/ }));
    await screen.findByText("fact 200");
    expect(screen.getByText(/\(showing 200\)/)).toBeInTheDocument();
    expect(new URL(calls[1].url).searchParams.get("offset")).toBe("100");

    fireEvent.click(screen.getByRole("button", { name: /Load more/ }));
    await screen.findByText("fact 230");
    await waitFor(() => expect(screen.queryByText(/showing/)).toBeNull());
    expect(screen.queryByRole("button", { name: /Load more/ })).toBeNull();
  });

  it("asks for every status, rows marked wrong included, when All status is chosen", async () => {
    render(<MemoryManagementSection />);
    await screen.findByText("fact 1");
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "all" } });
    fireEvent.click(screen.getByRole("button", { name: /Apply Filters/ }));
    await waitFor(() => expect(calls.length).toBe(2));
    expect(new URL(calls[1].url).searchParams.get("status")).toBe("all");
    expect(await screen.findByText("fact 10")).toBeInTheDocument();
  });
});
