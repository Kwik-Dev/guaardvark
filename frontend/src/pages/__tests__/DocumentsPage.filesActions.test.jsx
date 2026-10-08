import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

const { axiosMock, messages } = vi.hoisted(() => ({
  axiosMock: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    interceptors: { response: { use: vi.fn(() => 1), eject: vi.fn() } },
  },
  messages: [],
}));

vi.mock("axios", () => ({ default: axiosMock }));
vi.mock("../../api/indexingService", () => ({ triggerIndexing: vi.fn(), indexBulk: vi.fn() }));
vi.mock("../../api/documentService", () => ({ reviewRepoScope: vi.fn() }));
vi.mock("../../components/images/ImageLightbox", () => ({ default: () => null }));
vi.mock("../../components/documents/CodeViewerModal", () => ({ default: () => null }));
vi.mock("../../components/documents/PdfViewerModal", () => ({ default: () => null }));
vi.mock("../../components/documents/DocxViewerModal", () => ({ default: () => null }));
vi.mock("../../components/documents/AudioPlayerModal", () => ({ default: () => null }));
vi.mock("../../components/documents/MediaPreviewOverlay", () => ({ default: () => null }));
vi.mock("../../components/modals/FilePropertiesModal", () => ({ default: () => null }));
vi.mock("../../components/modals/FolderPropertiesModal", () => ({ default: () => null }));
vi.mock("../../components/branding", () => ({ BrandLogo: () => null }));
vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children, actions }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
vi.mock("../../contexts/StatusContext", () => ({
  useStatus: () => ({ activeModel: "test-model", isLoadingModel: false, modelError: null }),
}));
vi.mock("../../contexts/LayoutContext", () => {
  const layout = { gridSettings: { RGL_WIDTH_PROP_PX: 1600, _CONTAINER_PADDING_PX: 8 } };
  return { useLayout: () => layout };
});
vi.mock("../../components/common/SnackbarProvider", () => {
  const api = { showMessage: (text, level) => messages.push({ text, level }) };
  return { useSnackbar: () => api };
});

import DocumentsPage from "../DocumentsPage";

const REPORTS = { id: 7, name: "Reports", path: "Reports", document_count: 0, subfolder_count: 0 };
const ARCHIVE = { id: 8, name: "Archive", path: "Archive", document_count: 1, subfolder_count: 0 };
const NOTES = { id: 31, filename: "notes.txt", name: "notes.txt", path: "notes.txt" };

let savedWindows;

const posts = () => axiosMock.post.mock.calls.map(([url, body]) => ({ url, body }));

beforeEach(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  messages.length = 0;
  savedWindows = null;
  axiosMock.get.mockReset();
  axiosMock.post.mockReset();
  axiosMock.get.mockImplementation(async (url, config = {}) => {
    const path = config.params?.path ?? new URLSearchParams(url.split("?")[1] || "").get("path");
    if (url.startsWith("/api/files/browse")) {
      if (!path || path === "/") {
        return { data: { data: { folders: [REPORTS, ARCHIVE], documents: [NOTES] } } };
      }
      const folder = [REPORTS, ARCHIVE].find((f) => f.path === path);
      return { data: { data: { folders: [], documents: [], folder_id: folder?.id } } };
    }
    throw new Error(`unexpected GET ${url}`);
  });
  axiosMock.post.mockResolvedValue({ data: { data: {} } });
  global.fetch = vi.fn(async (url, opts = {}) => {
    if ((opts.method || "GET") === "POST") return { ok: true, json: async () => ({}) };
    if (!savedWindows) return { ok: false, json: async () => ({}) };
    return { ok: true, json: async () => savedWindows };
  });
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <DocumentsPage />
    </MemoryRouter>,
  );

const openReportsWindow = () => {
  savedWindows = {
    windows: [{ folderId: 7, state: "maximized" }],
    windowLayout: [{ i: "window-0", x: 0, y: 0, w: 30, h: 40 }],
  };
};

const menuItem = (name) => screen.getByRole("menuitem", { name });

describe("Files page paste target", () => {
  it("pastes into a folder window's folder from that window's right-click menu", async () => {
    openReportsWindow();
    renderPage();

    fireEvent.contextMenu(await screen.findByText("notes.txt"));
    fireEvent.click(menuItem("Copy"));
    fireEvent.contextMenu(await screen.findByText("This folder is empty"));
    fireEvent.click(menuItem("Paste"));

    await waitFor(() => expect(posts()).toContainEqual({
      url: "/api/files/document/31/copy",
      body: { destination_path: "Reports" },
    }));
    expect(messages.at(-1)).toEqual({ text: "Pasted 1 item(s) into Reports", level: "success" });
  });

  it("pastes into the right-clicked folder icon", async () => {
    renderPage();

    fireEvent.contextMenu(await screen.findByText("notes.txt"));
    fireEvent.click(menuItem("Cut"));
    fireEvent.contextMenu(screen.getByText("Archive"));
    fireEvent.click(menuItem("Paste"));

    await waitFor(() => expect(posts()).toContainEqual({
      url: "/api/files/document/31/move",
      body: { destination_path: "Archive" },
    }));
  });

  it("copies a folder with its contents through the folder copy route", async () => {
    openReportsWindow();
    renderPage();

    fireEvent.contextMenu(await screen.findByText("Archive"));
    fireEvent.click(menuItem("Copy"));
    fireEvent.contextMenu(await screen.findByText("This folder is empty"));
    fireEvent.click(menuItem("Paste"));

    await waitFor(() => expect(posts()).toContainEqual({
      url: "/api/files/folder/8/copy",
      body: { target_folder_id: 7 },
    }));
    expect(posts().some((p) => p.url === "/api/files/folder")).toBe(false);
  });

  it("reports a failed paste instead of claiming success", async () => {
    renderPage();
    axiosMock.post.mockRejectedValue({ response: { data: { message: "Cannot move folder into its own subfolder" } } });

    fireEvent.contextMenu(await screen.findByText("Archive"));
    fireEvent.click(menuItem("Cut"));
    fireEvent.contextMenu(screen.getByText("Archive"));
    fireEvent.click(menuItem("Paste"));

    await waitFor(() => expect(messages.at(-1)).toEqual({
      text: "Move failed: Cannot move folder into its own subfolder",
      level: "error",
    }));
  });
});

describe("Files page window state", () => {
  it("keeps an open folder window in the state saved while the page loads", async () => {
    openReportsWindow();
    renderPage();
    await screen.findByText("This folder is empty");

    const saves = () => global.fetch.mock.calls
      .filter(([, opts]) => opts?.method === "POST")
      .map(([, opts]) => JSON.parse(opts.body));
    await waitFor(() => expect(saves().length).toBeGreaterThan(0));
    for (const saved of saves()) {
      expect(saved.windows).toContainEqual(expect.objectContaining({ folderId: 7, state: "maximized" }));
    }
  });
});

describe("Files page import target", () => {
  const pickFiles = (container, names) => {
    const input = container.querySelector('input[type="file"]');
    fireEvent.change(input, { target: { files: names.map((n) => new File([n], n)) } });
  };
  const uploadFolders = () =>
    posts().filter((p) => p.url === "/api/files/upload").map((p) => p.body.get("folder_path"));

  it("toolbar Import goes to the desktop even after a folder was right-clicked", async () => {
    const { container } = renderPage();

    fireEvent.contextMenu(await screen.findByText("Archive"));
    fireEvent.click(menuItem("Copy"));
    fireEvent.click(screen.getByRole("button", { name: "Import Files" }));
    pickFiles(container, ["a.txt"]);

    await waitFor(() => expect(uploadFolders()).toEqual(["/"]));
  });

  it("Import Files from a window's menu goes into that window's folder", async () => {
    openReportsWindow();
    const { container } = renderPage();

    fireEvent.contextMenu(await screen.findByText("This folder is empty"));
    fireEvent.click(menuItem("Import Files"));
    pickFiles(container, ["a.txt", "b.txt"]);

    await waitFor(() => expect(uploadFolders()).toEqual(["Reports", "Reports"]));
  });
});

describe("Files page operating-system drop", () => {
  const fileEntry = (name) => {
    const file = new File([name], name);
    return { isFile: true, isDirectory: false, name, file: (ok) => setTimeout(() => ok(file), 0) };
  };

  // The browser's item list is one object that reads as empty once the drop
  // handler has yielded.
  const dropTransfer = (entries) => {
    let live = true;
    const items = {
      get length() {
        return live ? entries.length : 0;
      },
    };
    entries.forEach((entry, index) => {
      Object.defineProperty(items, index, {
        get: () => (live ? { kind: "file", webkitGetAsEntry: () => entry, getAsFile: () => null } : undefined),
      });
    });
    queueMicrotask(() => { live = false; });
    return { types: ["Files"], items, files: [] };
  };

  it("uploads every file of a multi-file drop", async () => {
    const { container } = renderPage();
    await screen.findByText("notes.txt");

    fireEvent.drop(container.querySelector("[data-desktop-container]"), {
      dataTransfer: dropTransfer([fileEntry("one.txt"), fileEntry("two.txt"), fileEntry("three.txt")]),
    });

    await waitFor(() => {
      const uploaded = posts().filter((p) => p.url === "/api/files/upload").map((p) => p.body.get("file").name);
      expect(uploaded).toEqual(["one.txt", "two.txt", "three.txt"]);
    });
    await waitFor(() => expect(messages.at(-1)).toEqual({ text: "Imported 3 file(s) into Files", level: "success" }));
  });
});
