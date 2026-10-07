import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TrainingDatasetModal from "./TrainingDatasetModal";
import * as trainingService from "../../api/trainingService";

vi.mock("../../api/trainingService");

const TRAINABLE = {
  path: "/home/u/sets/notes.jsonl",
  kind: "file",
  files: [{ path: "/home/u/sets/notes.jsonl", rows: 3, usable: 2 }],
  rows: 3,
  usable: 2,
  formats: { alpaca: 2 },
  samples: [{ format: "alpaca", messages: [{ role: "user", content: "Name a colour." }, { role: "assistant", content: "Blue." }] }],
  errors: ["notes.jsonl line 3: not valid JSON"],
  error_count: 1,
  trainable: true,
  reason: null,
};

const typePath = (value) =>
  fireEvent.change(screen.getByLabelText(/File or folder/), { target: { name: "path", value } });

describe("TrainingDatasetModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    trainingService.getTrainingDatasetLocations.mockResolvedValue({ default: "/data/training/datasets" });
    trainingService.inspectTrainingDataset.mockResolvedValue(TRAINABLE);
  });

  it("asks for a file or folder, never a URL", () => {
    render(<TrainingDatasetModal open onClose={() => {}} onSave={() => {}} />);
    expect(screen.getByLabelText(/File or folder \(\.jsonl or \.json\)/)).toBeInTheDocument();
    expect(screen.queryByText(/URL/)).not.toBeInTheDocument();
  });

  it("shows what the trainer will read and saves once it is trainable", async () => {
    const onSave = vi.fn();
    render(<TrainingDatasetModal open onClose={() => {}} onSave={onSave} />);
    fireEvent.change(screen.getByLabelText(/Dataset Name/), { target: { name: "name", value: "Notes" } });
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();

    typePath("~/sets/notes.jsonl");

    expect(await screen.findByText(/2 of 3 rows can be trained on \(2 instruction\/output\)/)).toBeInTheDocument();
    expect(screen.getByText("notes.jsonl line 3: not valid JSON")).toBeInTheDocument();
    expect(screen.getByText("Blue.")).toBeInTheDocument();
    expect(trainingService.inspectTrainingDataset).toHaveBeenCalledWith("~/sets/notes.jsonl");
    await waitFor(() => expect(save).toBeEnabled());

    fireEvent.click(save);
    expect(onSave).toHaveBeenCalledWith({ name: "Notes", description: "", path: "/home/u/sets/notes.jsonl" });
  });

  it("keeps Save off and says why for a path that cannot be trained on", async () => {
    trainingService.inspectTrainingDataset.mockResolvedValue({
      trainable: false,
      reason: "its path is a URL (https://x.test/d.jsonl); training reads .jsonl or .json files on this machine",
    });
    render(<TrainingDatasetModal open onClose={() => {}} onSave={() => {}} />);
    fireEvent.change(screen.getByLabelText(/Dataset Name/), { target: { name: "name", value: "Web" } });
    typePath("https://x.test/d.jsonl");

    expect(await screen.findByText(/This cannot be trained on: its path is a URL/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("only the newest path's answer is shown", async () => {
    let first;
    trainingService.inspectTrainingDataset
      .mockImplementationOnce(() => new Promise((r) => { first = r; }))
      .mockResolvedValueOnce(TRAINABLE);
    render(<TrainingDatasetModal open onClose={() => {}} onSave={() => {}} />);

    typePath("/old.jsonl");
    await waitFor(() => expect(trainingService.inspectTrainingDataset).toHaveBeenCalledTimes(1));
    typePath("/home/u/sets/notes.jsonl");
    await screen.findByText(/2 of 3 rows can be trained on/);
    first({ trainable: false, reason: "stale answer" });
    await new Promise((r) => setTimeout(r, 20));

    expect(screen.queryByText(/stale answer/)).not.toBeInTheDocument();
  });

  it("an existing dataset can be renamed while its saved path is kept", async () => {
    trainingService.inspectTrainingDataset.mockResolvedValue({ trainable: false, reason: "does not exist" });
    const onSave = vi.fn();
    const dataset = { id: 4, name: "Old", description: "", path: "https://x.test/old.jsonl" };
    render(<TrainingDatasetModal open onClose={() => {}} onSave={onSave} datasetData={dataset} />);

    fireEvent.change(screen.getByLabelText(/Dataset Name/), { target: { name: "name", value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith({ name: "Renamed", description: "", path: "https://x.test/old.jsonl" });
  });

  it("a prefill from a finished parse starts a new dataset", async () => {
    render(
      <TrainingDatasetModal
        open
        onClose={() => {}}
        onSave={() => {}}
        prefill={{ name: "Parsed notes", path: "/data/training/datasets/training_corpus.jsonl" }}
      />,
    );
    expect(screen.getByText("Add New Dataset")).toBeInTheDocument();
    expect(screen.getByLabelText(/Dataset Name/)).toHaveValue("Parsed notes");
    await waitFor(() =>
      expect(trainingService.inspectTrainingDataset).toHaveBeenCalledWith("/data/training/datasets/training_corpus.jsonl"),
    );
  });
});
