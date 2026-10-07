import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import NewTrainingJobModal from "./NewTrainingJobModal";
import * as trainingService from "../../api/trainingService";

vi.mock("../../api/trainingService");

const MODEL = {
  id: "Qwen/Qwen2.5-1.5B-Instruct",
  name: "Qwen2.5 1.5B Instruct",
  installed: true,
  fits: true,
  fit_reason: "fits this GPU (16 GB)",
  size_gb: 3.1,
  license: "Apache-2.0",
  max_batch_size: 4,
  max_seq_length: 2048,
  vision: false,
};
const SMALL = { ...MODEL, id: "Qwen/Qwen2.5-0.5B-Instruct", name: "Qwen2.5 0.5B Instruct", installed: false, size_gb: 1 };
const HUGE = { ...MODEL, id: "org/huge", name: "Huge", fits: false, fit_reason: "needs about 40 GB" };

const openSelect = (label) => fireEvent.mouseDown(within(screen.getByText(label).closest(".MuiFormControl-root")).getByRole("combobox"));

describe("NewTrainingJobModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    trainingService.getTrainingDatasets.mockResolvedValue([{ id: 7, name: "Notes" }]);
    trainingService.getDeviceProfiles.mockResolvedValue([]);
    trainingService.getBaseModels.mockResolvedValue({ models: [SMALL, MODEL, HUGE] });
    trainingService.getImageFolders.mockResolvedValue([]);
    trainingService.getHardwareCapabilities.mockResolvedValue({
      gpu_name: "test card",
      vram_total_mb: 16376,
      recommended_config: { batch_size: 4, max_seq_length: 4096, lora_rank: 16 },
    });
    trainingService.getTrainingLibraries.mockResolvedValue({ ready: true, libraries: [] });
  });

  it("offers only models that fit, with the ones not downloaded shown but not choosable", async () => {
    render(<NewTrainingJobModal open onClose={() => {}} onSave={() => {}} />);
    await waitFor(() => expect(screen.getByText("Qwen2.5 1.5B Instruct")).toBeInTheDocument());

    openSelect("Base Model");
    const listbox = await screen.findByRole("listbox");
    const options = within(listbox).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([
      expect.stringContaining("Qwen2.5 0.5B Instruct"),
      expect.stringContaining("Qwen2.5 1.5B Instruct"),
    ]);
    expect(options[0]).toHaveAttribute("aria-disabled", "true");
    expect(within(listbox).queryByText("Huge")).not.toBeInTheDocument();
    expect(screen.getByText(/Qwen2\.5 0\.5B Instruct is not\s+downloaded/)).toBeInTheDocument();
    expect(screen.queryByText(/This job will download/)).not.toBeInTheDocument();
  });

  it("hides the vision choice until a vision model is declared", async () => {
    render(<NewTrainingJobModal open onClose={() => {}} onSave={() => {}} />);
    await waitFor(() => expect(trainingService.getBaseModels).toHaveBeenCalled());
    expect(screen.queryByText("Vision Fine-Tuning")).not.toBeInTheDocument();
  });

  it("keeps the other lists when one of them fails to load, and says which", async () => {
    trainingService.getDeviceProfiles.mockRejectedValue(new Error("server said no"));
    render(<NewTrainingJobModal open onClose={() => {}} onSave={() => {}} />);

    expect(await screen.findByText(/Could not load device profiles \(server said no\)/)).toBeInTheDocument();
    openSelect("Dataset");
    expect(await screen.findByRole("option", { name: "Notes" })).toBeInTheDocument();
  });

  it("leaves steps to the backend when empty and clamps settings to the model", async () => {
    const onSave = vi.fn();
    render(<NewTrainingJobModal open onClose={() => {}} onSave={onSave} />);
    await waitFor(() => expect(screen.getByText("Qwen2.5 1.5B Instruct")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/Job Name/), { target: { name: "name", value: "first run" } });
    openSelect("Dataset");
    fireEvent.click(await screen.findByRole("option", { name: "Notes" }));

    fireEvent.click(screen.getByRole("button", { name: "Create Job" }));

    const job = onSave.mock.calls[0][0];
    expect(job.base_model).toBe(MODEL.id);
    expect(job.dataset_id).toBe(7);
    expect(job.config).not.toHaveProperty("steps");
    expect(job.config).not.toHaveProperty("cpu_offload");
    expect(job.config.seq_length).toBe(2048);
    expect(job.config.batch_size).toBe(4);
  });

  it("sends steps a person typed", async () => {
    const onSave = vi.fn();
    render(<NewTrainingJobModal open onClose={() => {}} onSave={onSave} />);
    await waitFor(() => expect(screen.getByText("Qwen2.5 1.5B Instruct")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/Job Name/), { target: { name: "name", value: "run" } });
    openSelect("Dataset");
    fireEvent.click(await screen.findByRole("option", { name: "Notes" }));
    fireEvent.change(screen.getByLabelText("Training Steps"), { target: { name: "config.steps", value: "40" } });

    fireEvent.click(screen.getByRole("button", { name: "Create Job" }));
    expect(onSave.mock.calls[0][0].config.steps).toBe(40);
  });

  it("cannot create a job until a base model is downloaded", async () => {
    trainingService.getBaseModels.mockResolvedValue({ models: [SMALL] });
    render(<NewTrainingJobModal open onClose={() => {}} onSave={() => {}} />);
    await waitFor(() => expect(trainingService.getBaseModels).toHaveBeenCalled());
    await screen.findByText(/is not\s+downloaded/);
    expect(screen.getByRole("button", { name: "Create Job" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Download base models" })).toBeInTheDocument();
  });
});
