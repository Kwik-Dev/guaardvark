import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const getStatus = vi.fn();
const updateConfig = vi.fn();
vi.mock("../../../api/claudeAdvisorService", () => ({
  claudeAdvisorService: {
    getStatus: (...a) => getStatus(...a),
    updateConfig: (...a) => updateConfig(...a),
    testConnection: vi.fn(),
  },
}));

import UncleClaudeSection from "../UncleClaudeSection";

const renderSection = () => render(<MemoryRouter><UncleClaudeSection /></MemoryRouter>);

beforeEach(() => {
  getStatus.mockReset();
  updateConfig.mockReset();
});

describe("Uncle Claude settings failures", () => {
  it("says a status read failed instead of claiming 'Not configured'", async () => {
    getStatus.mockRejectedValue(new Error("HTTP error 502 - Bad Gateway"));
    renderSection();
    expect(
      await screen.findByText("Uncle Claude's status could not be read: HTTP error 502 - Bad Gateway."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Not configured")).not.toBeInTheDocument();
    expect(screen.getByText("Status unknown")).toBeInTheDocument();
  });

  it("says a failed escalation save did not change anything", async () => {
    getStatus.mockResolvedValue({ data: { available: true, escalation_mode: "manual", usage: {} } });
    updateConfig.mockRejectedValue(new Error("config is read-only"));
    renderSection();

    fireEvent.mouseDown(await screen.findByRole("combobox"));
    fireEvent.click(within(await screen.findByRole("listbox")).getByText("Smart (auto when local fails)"));

    expect(await screen.findByText("Escalation was not changed: config is read-only.")).toBeInTheDocument();
  });
});
