import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";

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

describe("UploadPage recent uploads", () => {
  const tick = async (ms) => {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  };

  it("refreshes while a row is indexing and stops once every row has settled", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const row = (status) => ({ id: 9, filename: "report.pdf", index_status: status, uploaded_at: null });
    api.getDocuments
      .mockResolvedValueOnce({ documents: [row("INDEXING")] })
      .mockResolvedValueOnce({ documents: [row("INDEXING")] })
      .mockResolvedValue({ documents: [row("INDEXED")] });
    render(<UploadPage />);
    expect(await screen.findByText("INDEXING")).toBeInTheDocument();
    expect(api.getDocuments).toHaveBeenCalledTimes(1);

    await tick(3000);
    expect(api.getDocuments).toHaveBeenCalledTimes(2);
    await tick(3000);
    expect(api.getDocuments).toHaveBeenCalledTimes(3);
    expect(await screen.findByText("INDEXED")).toBeInTheDocument();

    await tick(9000);
    expect(api.getDocuments).toHaveBeenCalledTimes(3);
  });

  it("does not poll when nothing is indexing", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    api.getDocuments.mockResolvedValue({
      documents: [{ id: 1, filename: "done.txt", index_status: "INDEXED", uploaded_at: null }],
    });
    render(<UploadPage />);
    await screen.findByText("done.txt");
    await tick(9000);
    expect(api.getDocuments).toHaveBeenCalledTimes(1);
  });
});

describe("UploadPage import", () => {
  it("uploads once and leaves indexing to the upload route", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const row = (status) => ({ id: 42, filename: "notes.txt", index_status: status, uploaded_at: null });
    api.getDocuments
      .mockResolvedValueOnce({ documents: [] })
      .mockResolvedValueOnce({ documents: [row("STORED")] })
      .mockResolvedValue({ documents: [row("INDEXED")] });
    api.uploadFile.mockResolvedValue({ document_id: 42, filename: "notes.txt", job_id: "up-1" });
    render(<UploadPage />);
    await screen.findByText("No recent uploads.");
    dropFile(screen.getByTestId("upload-drop-zone"), textFile("notes.txt"));
    fireEvent.click(screen.getByRole("button", { name: "Import and Index" }));

    expect(await screen.findByText(/uploaded \(ID: 42\); indexing queued/)).toBeInTheDocument();
    expect(api.uploadFile).toHaveBeenCalledTimes(1);
    expect(indexing.triggerIndexing).not.toHaveBeenCalled();

    // The just-uploaded row is followed until it settles.
    expect(await screen.findByText("STORED")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(await screen.findByText("INDEXED")).toBeInTheDocument();
    const calls = api.getDocuments.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });
    expect(api.getDocuments).toHaveBeenCalledTimes(calls);
  });
});
