import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../../../api", () => ({
  getProjects: vi.fn(async () => [{ id: 7, name: "Launch" }]),
  getAvailableModels: vi.fn(async () => []),
}));
vi.mock("../../../api/ruleService", () => ({ getRules: vi.fn(async () => []) }));
vi.mock("../../../api/websiteService", () => ({
  getWebsites: vi.fn(async () => [
    { id: 5, url: "https://example.com", client: { id: 3, name: "Acme" }, project: { id: 7, name: "Launch" } },
  ]),
}));
vi.mock("../../../api/bulkGenerationService", () => ({ generateStructuredCSV: vi.fn(async () => ({})) }));
vi.mock("../../../contexts/UnifiedProgressContext", () => ({
  useUnifiedProgress: () => ({ getProcess: () => null }),
}));

import TaskActionModal from "../TaskActionModal";

beforeEach(() => {
  global.fetch = vi.fn(async () => ({ ok: true, json: async () => ({}) }));
});

describe("TaskActionModal defaults for a new task", () => {
  it("picks the linked website and saves the task linked to it and its client", async () => {
    const onSave = vi.fn(async () => ({ id: 99 }));
    render(
      <TaskActionModal open taskData={null} defaults={{ website_id: 5 }} onSave={onSave} onClose={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText(/Client Name/)).toHaveValue("Acme"));
    expect(screen.getByLabelText(/Target Website/)).toHaveValue("https://example.com");

    fireEvent.change(screen.getByLabelText(/Task Name/), { target: { name: "name", value: "Site copy" } });
    fireEvent.click(screen.getByRole("button", { name: /Create/ }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const [id, payload] = onSave.mock.calls[0];
    expect(id).toBeNull();
    expect(payload).toMatchObject({ client_id: 3, website_id: 5, project_id: 7, client_name: "Acme" });
  });

  it("fills the client name from a client link", async () => {
    render(
      <TaskActionModal
        open
        taskData={null}
        defaults={{ client_id: 3, client_name: "Acme" }}
        onSave={vi.fn()}
        onClose={() => {}}
      />,
    );
    await waitFor(() => expect(screen.getByLabelText(/Client Name/)).toHaveValue("Acme"));
  });
});
