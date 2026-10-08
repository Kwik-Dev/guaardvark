import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../../api", () => ({
  getProject: vi.fn(async () => ({ id: 7, name: "Launch", description: "" })),
  getClients: vi.fn(async () => []),
  getTasks: vi.fn(async () => []),
  getDocuments: vi.fn(async () => ({ documents: [{ id: 1, filename: "brief.pdf", index_status: "INDEXED" }] })),
  getWebsites: vi.fn(async () => []),
  getRules: vi.fn(async () => []),
  createTask: vi.fn(),
  createWebsite: vi.fn(),
  deleteDocument: vi.fn(),
  deleteProject: vi.fn(),
  linkRuleToProject: vi.fn(),
  unlinkRuleFromProject: vi.fn(),
  updateProject: vi.fn(),
}));
vi.mock("../../api/indexingService", () => ({ triggerIndexing: vi.fn() }));
vi.mock("../../components/modals/LinkingModal", () => ({ default: () => null }));
vi.mock("../../components/modals/WebsiteActionModal", () => ({ default: () => null }));
vi.mock("../../components/common/SnackbarProvider", () => ({ useSnackbar: () => ({ showMessage: () => {} }) }));
vi.mock("../../contexts/StatusContext", () => ({
  useStatus: () => ({ activeModel: "m", isLoadingModel: false, modelError: null }),
}));
vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children, actions }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));

import ProjectDetailPage from "../ProjectDetailPage";

const renderAt = (url) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );

describe("ProjectDetailPage ?tab=", () => {
  it("opens the Documents tab for ?tab=documents", async () => {
    renderAt("/projects/7?tab=documents");
    expect(await screen.findByRole("tab", { name: "Documents" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("brief.pdf")).toBeInTheDocument();
  });

  it("opens the Tasks tab without the parameter", async () => {
    renderAt("/projects/7");
    expect(await screen.findByRole("tab", { name: "Tasks" })).toHaveAttribute("aria-selected", "true");
  });
});
