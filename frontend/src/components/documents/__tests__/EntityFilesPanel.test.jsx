import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

const getDocuments = vi.fn();
vi.mock("../../../api/documentService", () => ({
  getDocuments: (...args) => getDocuments(...args),
}));

import EntityFilesPanel from "../EntityFilesPanel";

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  getDocuments.mockReset();
  vi.stubGlobal("fetch", vi.fn(async (url) => (
    String(url).includes("/clients/3") ? json({ id: 3, name: "Acme" }) : json({ error: "Not found" }, 404)
  )));
});

describe("Files page filtered to one client or website", () => {
  it("lists the client's documents by name and folder, and opens one on click", async () => {
    getDocuments.mockResolvedValue({
      documents: [
        { id: 1, filename: "brief.pdf", folder: { path: "Clients/Acme" } },
        { id: 2, filename: "logo.png", folder: null },
      ],
    });
    const onOpenFile = vi.fn();
    render(<EntityFilesPanel kind="client" id={3} onOpenFile={onOpenFile} onClose={() => {}} />);

    expect(await screen.findByText("Files for client Acme")).toBeInTheDocument();
    expect(getDocuments).toHaveBeenCalledWith(expect.objectContaining({ client_id: 3 }));
    expect(screen.getByText("Clients/Acme")).toBeInTheDocument();
    expect(screen.getByText("Files root")).toBeInTheDocument();

    fireEvent.click(screen.getByText("logo.png"));
    expect(onOpenFile).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }));
  });

  it("says how to link files when the website has none, and closes", async () => {
    getDocuments.mockResolvedValue({ documents: [] });
    const onClose = vi.fn();
    render(<EntityFilesPanel kind="website" id={5} onOpenFile={() => {}} onClose={onClose} />);

    expect(await screen.findByText(/No files are linked to this website/)).toBeInTheDocument();
    expect(getDocuments).toHaveBeenCalledWith(expect.objectContaining({ website_id: 5 }));
    expect(screen.getByText("Files for this website")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });
});
