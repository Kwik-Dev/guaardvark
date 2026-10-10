import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@monaco-editor/react", () => ({
  default: ({ value, options }) => (
    <textarea data-testid="editor" readOnly={options.readOnly} defaultValue={value} />
  ),
}));
vi.mock("../../../api/documentService", () => ({
  getDocumentContent: vi.fn(async () => ({ content: "print('hi')" })),
  getRepoFileContent: vi.fn(async () => ({ content: "def main(): ..." })),
  updateDocument: vi.fn(),
}));

import CodeViewerModal from "../CodeViewerModal";

const DOC = { id: 31, filename: "notes.txt" };
const REPO_FILE = { id: "repo:backend/app.py", filename: "app.py", source_type: "live_repo", relative_path: "backend/app.py" };

describe("CodeViewerModal", () => {
  it("opens read-only by default", async () => {
    render(<CodeViewerModal open onClose={() => {}} file={DOC} />);
    expect((await screen.findByTestId("editor")).readOnly).toBe(true);
  });

  it("opens in edit mode when asked (the Files page's Edit action)", async () => {
    render(<CodeViewerModal open onClose={() => {}} file={DOC} startEditing />);
    expect((await screen.findByTestId("editor")).readOnly).toBe(false);
  });

  it("keeps a live-repo file read-only even when asked to edit", async () => {
    render(<CodeViewerModal open onClose={() => {}} file={REPO_FILE} startEditing />);
    expect((await screen.findByTestId("editor")).readOnly).toBe(true);
  });
});
