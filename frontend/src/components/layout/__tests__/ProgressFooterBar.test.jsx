import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";

const progress = { activeProcesses: new Map(), unifiedJobs: new Map(), connectionState: "connected" };
const listActiveJobs = vi.fn();

vi.mock("../../../contexts/UnifiedProgressContext", () => ({
  useUnifiedProgress: () => progress,
}));
vi.mock("../../../api/jobsService", () => ({
  listActiveJobs: (...args) => listActiveJobs(...args),
}));
vi.mock("../TaskQueueIndicator", () => ({ default: () => null }));
vi.mock("../../../stores/useAppStore", () => ({
  useAppStore: (select) => select({ sidebarExpanded: false, navChrome: "software" }),
}));

const { default: ProgressFooterBar } = await import("../ProgressFooterBar");

const renderBar = () =>
  render(
    <ThemeProvider theme={createTheme()}>
      <ProgressFooterBar />
    </ThemeProvider>,
  );

const waitingImage = {
  job_id: "ImageBatch_1",
  processType: "image_generation",
  status: "processing",
  progress: 0,
  message: "Queued behind Video Gen — needs ~11.7 GB, 2.6 GB free",
  timestamp: 200,
  additional_data: { batch_id: "ImageBatch_1", gpu_wait_reason: "Queued behind Video Gen — needs ~11.7 GB, 2.6 GB free" },
};
const videoBatch = {
  id: "video_gen:VideoBatch_1",
  kind: "video_gen",
  native_id: "VideoBatch_1",
  status: "running",
  progress: 5,
  _receivedAt: Date.now() + 60000,
  metadata: { batch_id: "VideoBatch_1", stage: "generate", current_item: "item-a", total_videos: 1 },
};
const denoising = {
  job_id: "item-a",
  processType: "video_render",
  status: "processing",
  progress: 40,
  message: "denoising 12/30",
  timestamp: 300,
  additional_data: { batch_id: "VideoBatch_1" },
};

describe("ProgressFooterBar", () => {
  beforeEach(() => {
    listActiveJobs.mockReset();
    listActiveJobs.mockResolvedValue({ jobs: [] });
    progress.activeProcesses = new Map();
    progress.unifiedJobs = new Map();
  });

  it("stays hidden with nothing in flight and seeds video batches on connect", async () => {
    const { container } = renderBar();
    await act(async () => {});
    expect(container).toBeEmptyDOMElement();
    expect(listActiveJobs).toHaveBeenCalledWith({ kinds: ["video_gen"], limit: 50 });
  });

  it("leads with the running video and shows the waiting image as a chip", async () => {
    progress.activeProcesses = new Map([[waitingImage.job_id, waitingImage], [denoising.job_id, denoising]]);
    progress.unifiedJobs = new Map([[videoBatch.id, videoBatch]]);
    renderBar();
    await act(async () => {});
    expect(screen.getByText("Video Gen: denoising 12/30")).toBeInTheDocument();
    expect(screen.getByText("40%")).toBeInTheDocument();
    expect(screen.getByText("Image: waiting for GPU")).toBeInTheDocument();
    expect(screen.queryByText(/Image Gen: Queued behind/)).not.toBeInTheDocument();
  });

  it("lists every job with its full wait reason on hover", async () => {
    progress.activeProcesses = new Map([[waitingImage.job_id, waitingImage], [denoising.job_id, denoising]]);
    progress.unifiedJobs = new Map([[videoBatch.id, videoBatch]]);
    renderBar();
    await act(async () => {});
    fireEvent.mouseEnter(screen.getByTestId("footer-jobs"));
    expect(await screen.findByText("Image Gen: Queued behind Video Gen — needs ~11.7 GB, 2.6 GB free")).toBeInTheDocument();
    expect(screen.getByText("Image Gen — waiting")).toBeInTheDocument();
  });

  it("shows a queued video batch's wait when it is the only job", async () => {
    progress.unifiedJobs = new Map([[videoBatch.id, {
      ...videoBatch,
      metadata: { ...videoBatch.metadata, stage: "gpu_wait", gpu_wait_reason: "Queued behind Image Gen — needs ~9.0 GB, 3.0 GB free" },
    }]]);
    renderBar();
    await act(async () => {});
    expect(screen.getByText("Video Gen: Queued behind Image Gen — needs ~9.0 GB, 3.0 GB free")).toBeInTheDocument();
  });
});
