import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const cancelJob = vi.fn(async () => ({ cancelled: true }));

// One Map for every render, as the real context provides; a new one per render
// would re-run the live-update effect forever.
const progress = vi.hoisted(() => ({ unifiedJobs: new Map() }));
vi.mock("../../contexts/UnifiedProgressContext", () => ({
  useUnifiedProgress: () => progress,
}));

vi.mock("../../api/jobsService", () => ({
  JOB_STATUSES: ["pending", "running", "paused", "completed", "failed", "cancelled"],
  listJobs: vi.fn(async () => ({
    jobs: [
      { id: "job-1", label: "Index docs", kind: "indexing", status: "running", cancellable: true },
      { id: "job-2", label: "Render clip", kind: "video", status: "completed", cancellable: false },
    ],
  })),
  listJobHistory: vi.fn(async () => ({ history: [] })),
  jobsSummary: vi.fn(async () => ({})),
  cancelJob: (...args) => cancelJob(...args),
  clearJobHistory: vi.fn(async () => ({})),
}));

vi.mock("../../api", () => ({
  getIndexingPaused: vi.fn(async () => false),
  resumePendingIndexing: vi.fn(async () => ({})),
}));

import JobsList from "./JobsList";

describe("JobsList row menu", () => {
  beforeEach(() => cancelJob.mockClear());

  it("offers Cancel only on cancellable jobs and runs the same cancel", async () => {
    render(<JobsList kinds={["indexing", "video"]} />);

    fireEvent.contextMenu(await screen.findByText("Render clip"), { clientX: 10, clientY: 10 });
    expect(screen.getAllByRole("menuitem").map((el) => el.textContent)).toEqual(["Details", "Copy ID"]);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });

    fireEvent.contextMenu(screen.getByText("Index docs"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Cancel job" }));
    await waitFor(() => expect(cancelJob).toHaveBeenCalledWith("job-1"));
    expect(await screen.findByText("cancelled")).toBeInTheDocument();
  });

  it("Details opens the job drawer", async () => {
    render(<JobsList kinds={["indexing", "video"]} />);
    fireEvent.contextMenu(await screen.findByText("Render clip"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Details" }));
    expect(await screen.findByText("Job detail")).toBeInTheDocument();
    expect(screen.getByText("job-2")).toBeInTheDocument();
  });
});
