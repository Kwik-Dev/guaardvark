import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

const { api, indexing } = vi.hoisted(() => ({
  api: { uploadFile: vi.fn(), getDocuments: vi.fn() },
  indexing: { triggerIndexing: vi.fn() },
}));

vi.mock("../../api", () => api);
vi.mock("../../api/indexingService", () => indexing);
vi.mock("../../contexts/StatusContext", () => ({
  useStatus: () => ({ activeModel: "test-model", isLoadingModel: false, modelError: null }),
}));
vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children }) => <div>{children}</div>,
}));

import UploadPage from "../UploadPage";

const textFile = (name) => new File(["hello"], name, { type: "text/plain" });

const dropFile = (zone, file) =>
  fireEvent.drop(zone, { dataTransfer: { files: [file], types: ["Files"], items: [] } });

beforeEach(() => {
  api.uploadFile.mockReset();
  api.getDocuments.mockReset();
  indexing.triggerIndexing.mockReset();
  api.getDocuments.mockResolvedValue({ documents: [] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("UploadPage drop zone", () => {
  it("takes a dropped file the same way as Select File", async () => {
    render(<UploadPage />);
    await screen.findByText("No recent uploads.");
    dropFile(screen.getByTestId("upload-drop-zone"), textFile("notes.txt"));
    expect(screen.getByText(/Selected: notes\.txt/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import and Index" })).toBeEnabled();
  });

  it("refuses a dropped file of an unsupported type", async () => {
    render(<UploadPage />);
    await screen.findByText("No recent uploads.");
    dropFile(screen.getByTestId("upload-drop-zone"), textFile("photo.exe"));
    expect(screen.getByText(/'photo\.exe' is not a supported file type/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import and Index" })).toBeDisabled();
  });

  it("opens the picker when the box itself is clicked", async () => {
    render(<UploadPage />);
    await screen.findByText("No recent uploads.");
    const input = document.getElementById("raised-button-file");
    const click = vi.spyOn(input, "click").mockImplementation(() => {});
    fireEvent.click(screen.getByTestId("upload-drop-zone"));
    expect(click).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Select File" }));
    expect(click).toHaveBeenCalledTimes(2);
  });
});
