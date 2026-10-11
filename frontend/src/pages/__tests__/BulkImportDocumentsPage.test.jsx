import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const { bulkApi, messages } = vi.hoisted(() => ({
  bulkApi: { startBulkImport: vi.fn(), getBulkImportStatus: vi.fn() },
  messages: [],
}));

vi.mock("../../api/apiClient", () => ({ BASE_URL: "/api" }));
vi.mock("../../api/bulkImportService", () => bulkApi);
vi.mock("../../api/clientService", () => ({ getClients: vi.fn(async () => []) }));
vi.mock("../../api/projectService", () => ({ getProjects: vi.fn(async () => []) }));
vi.mock("../../api/websiteService", () => ({ getWebsites: vi.fn(async () => []) }));
vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children, actions }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
vi.mock("../../components/common/SnackbarProvider", () => {
  const api = { showMessage: (text, level) => messages.push({ text, level }) };
  return { useSnackbar: () => api };
});

import BulkImportDocumentsPage from "../BulkImportDocumentsPage";

const renderPage = () =>
  render(
    <MemoryRouter>
      <BulkImportDocumentsPage />
    </MemoryRouter>,
  );

beforeEach(() => {
  messages.length = 0;
  bulkApi.startBulkImport.mockReset();
  bulkApi.getBulkImportStatus.mockReset();
  global.fetch = vi.fn();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("BulkImportDocumentsPage options", () => {
  it("copies by default and says so; unticked, it says files are moved", async () => {
    renderPage();
    const copyBox = screen.getByLabelText("Copy files instead of moving them");
    expect(copyBox).toBeChecked();
    expect(screen.getByText(/the source folder is left as it is \(the files take disk space twice\)/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Source Directory/), { target: { value: "/data/in" } });
    fireEvent.click(screen.getByRole("button", { name: "Start Import" }));
    expect(
      await screen.findByText("Files are copied; the source folder is left as it is."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    fireEvent.click(copyBox);
    expect(copyBox).not.toBeChecked();
    expect(screen.getByText(/moved out of the source folder into Documents/)).toBeInTheDocument();
    // The cancelled confirm dialog keeps the page aria-hidden until its exit transition ends.
    fireEvent.click(await screen.findByRole("button", { name: "Start Import" }));
    expect(await screen.findByText("Files are moved out of the source folder.")).toBeInTheDocument();
  });
});

describe("BulkImportDocumentsPage reindex option", () => {
  it("is named for what the worker does: only unindexed existing files are queued", () => {
    renderPage();
    const box = screen.getByLabelText("Index already-imported files that aren't indexed yet");
    expect(box).toBeChecked();
    expect(screen.getByText(/already indexed are not indexed again/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Re-index existing documents/)).not.toBeInTheDocument();
  });
});

describe("BulkImportDocumentsPage job status", () => {
  const startDryRun = async () => {
    bulkApi.startBulkImport.mockResolvedValue({ job_id: "job-1", status: "processing" });
    renderPage();
    fireEvent.change(screen.getByLabelText(/Source Directory/), { target: { value: "/data/in" } });
    fireEvent.click(screen.getByLabelText(/Dry run/));
    fireEvent.click(screen.getByRole("button", { name: "Preview Import" }));
    expect(await screen.findByText("Polling...")).toBeInTheDocument();
  };

  it("stops polling and turns green when the worker reports \"complete\"", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    await startDryRun();
    bulkApi.getBulkImportStatus.mockResolvedValue({
      job_id: "job-1",
      status: "complete",
      message: "Dry run: Would import 2 files",
      progress: 100,
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(await screen.findByText("Dry run: Would import 2 files")).toBeInTheDocument();
    expect(screen.queryByText("Polling...")).not.toBeInTheDocument();
    const alert = screen.getByText("Job Status").closest(".MuiAlert-root");
    expect(alert.className).toMatch(/Success/);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(bulkApi.getBulkImportStatus).toHaveBeenCalledTimes(1);
  });

  it("Reset stops the poll and clears the job", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    await startDryRun();
    bulkApi.getBulkImportStatus.mockResolvedValue({ job_id: "job-1", status: "processing" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(bulkApi.getBulkImportStatus).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(screen.queryByText("Polling...")).not.toBeInTheDocument();
    expect(screen.getByText(/No import job running/)).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });
    expect(bulkApi.getBulkImportStatus).toHaveBeenCalledTimes(1);
  });

  it("shows a complete job with failed files as finished with errors, not green", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    await startDryRun();
    bulkApi.getBulkImportStatus.mockResolvedValue({
      job_id: "job-1",
      status: "complete",
      message: "Import complete: 1 imported, 0 skipped, 2 errors",
      stats: { total_files: 3, imported: 1, skipped: 0, errors: 2, queued_for_indexing: 1 },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(await screen.findByText("Finished with errors (2 failed)")).toBeInTheDocument();
    const alert = screen.getByText("Finished with errors (2 failed)").closest(".MuiAlert-root");
    expect(alert.className).toMatch(/Warning/);
    expect(alert.className).not.toMatch(/Success/);
    expect(screen.queryByText("Polling...")).not.toBeInTheDocument();
  });

  it("also accepts \"completed\"", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    await startDryRun();
    bulkApi.getBulkImportStatus.mockResolvedValue({ job_id: "job-1", status: "completed" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(await screen.findByText("completed")).toBeInTheDocument();
    expect(screen.queryByText("Polling...")).not.toBeInTheDocument();
  });
});
