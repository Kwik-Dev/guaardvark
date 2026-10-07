import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DirectoryPicker from "./DirectoryPicker";

// A small server tree; "~" is /home/u as the backend resolves it.
const TREE = {
  "/home/u": { dirs: ["sets", "docs"], files: [{ name: "readme.md", size: 12, extension: "md" }] },
  "/home/u/sets": {
    dirs: [],
    files: [
      { name: "a.jsonl", size: 2048, extension: "jsonl" },
      { name: "notes.txt", size: 10, extension: "txt" },
    ],
  },
  "/home/u/docs": { dirs: [], files: [] },
};
const FILES = new Set(["/home/u/sets/a.jsonl", "/home/u/sets/notes.txt"]);

const resolve = (path) => (path === "~" ? "/home/u" : path.startsWith("~/") ? `/home/u${path.slice(1)}` : path);

const json = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function answer(url) {
  const params = new URL(url, "http://localhost").searchParams;
  const path = resolve(params.get("path"));
  const node = TREE[path];
  if (node) {
    const cut = path.lastIndexOf("/");
    return json(200, {
      path,
      directories: node.dirs.map((name) => ({ name, item_count: 0 })),
      parent_path: path === "/" ? null : path.slice(0, cut) || "/",
      ...(params.get("include_files") === "true" ? { files: node.files } : {}),
    });
  }
  if (FILES.has(path)) return json(400, { error: "Path is not a directory" });
  return json(404, { error: "Path does not exist" });
}

const requested = () =>
  global.fetch.mock.calls.map(([url]) => Object.fromEntries(new URL(url, "http://localhost").searchParams));

describe("DirectoryPicker", () => {
  beforeEach(() => {
    global.fetch = vi.fn(async (url) => answer(url));
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("opens in the home folder by default and Home asks the server for ~", async () => {
    render(<DirectoryPicker open onClose={() => {}} onSelect={() => {}} />);

    await screen.findByText("sets");
    expect(requested()[0]).toMatchObject({ path: "~", show_hidden: "false" });
    expect(screen.getByLabelText("Path")).toHaveValue("/home/u");

    fireEvent.click(screen.getByRole("button", { name: /^docs/ }));
    await waitFor(() => expect(screen.getByLabelText("Path")).toHaveValue("/home/u/docs"));
    fireEvent.click(screen.getByLabelText("Home folder"));
    await screen.findByText("sets");
    expect(requested().at(-1).path).toBe("~");
  });

  it("picks a file of the listed types and leaves other files alone", async () => {
    const onSelect = vi.fn();
    render(
      <DirectoryPicker
        open
        onClose={() => {}}
        onSelect={onSelect}
        initialPath="/home/u/sets"
        mode="fileOrFolder"
        fileExtensions={[".jsonl", ".json"]}
      />,
    );

    const file = await screen.findByRole("button", { name: /a\.jsonl/ });
    expect(screen.getByRole("button", { name: /notes\.txt/ })).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(file);
    fireEvent.click(screen.getByRole("button", { name: "Select: a.jsonl" }));

    expect(onSelect).toHaveBeenCalledWith("/home/u/sets/a.jsonl", { kind: "file" });
  });

  it("in file mode Select waits for a file", async () => {
    render(
      <DirectoryPicker open onClose={() => {}} onSelect={() => {}} initialPath="/home/u/sets" mode="file" />,
    );
    await screen.findByText("a.jsonl");
    expect(screen.getByRole("button", { name: "Choose a file" })).toBeDisabled();
  });

  it("a starting path that is a file opens its folder with the file selected", async () => {
    render(
      <DirectoryPicker
        open
        onClose={() => {}}
        onSelect={() => {}}
        initialPath="~/sets/a.jsonl"
        mode="fileOrFolder"
        fileExtensions={[".jsonl"]}
      />,
    );

    expect(await screen.findByRole("button", { name: "Select: a.jsonl" })).toBeEnabled();
    expect(requested().map((r) => r.path)).toEqual(["~/sets/a.jsonl", "~/sets"]);
  });

  it("a starting path that does not exist falls back and says so", async () => {
    render(<DirectoryPicker open onClose={() => {}} onSelect={() => {}} initialPath="/gone" />);
    await screen.findByText("sets");
    expect(screen.getByText(/\/gone was not found; showing your home folder/)).toBeInTheDocument();
  });

  it("a typed path must be opened before Select uses it", async () => {
    const onSelect = vi.fn();
    render(<DirectoryPicker open onClose={() => {}} onSelect={onSelect} />);
    await screen.findByText("sets");

    const field = screen.getByLabelText("Path");
    fireEvent.change(field, { target: { value: "/home/u/sets" } });
    expect(screen.getByRole("button", { name: "Select: u" })).toBeDisabled();

    fireEvent.keyPress(field, { key: "Enter", code: "Enter", charCode: 13 });
    const select = await screen.findByRole("button", { name: "Select: sets" });
    fireEvent.click(select);
    expect(onSelect).toHaveBeenCalledWith("/home/u/sets", { kind: "folder" });
  });

  it("a late answer for an earlier folder does not replace the newer one", async () => {
    render(<DirectoryPicker open onClose={() => {}} onSelect={() => {}} />);
    await screen.findByText("sets");

    let releaseSets;
    global.fetch.mockImplementation(async (url) => {
      if (resolve(new URL(url, "http://localhost").searchParams.get("path")) === "/home/u/sets") {
        await new Promise((r) => { releaseSets = r; });
      }
      return answer(url);
    });
    fireEvent.click(screen.getByRole("button", { name: /^sets/ }));
    fireEvent.click(screen.getByLabelText("Home folder"));
    await screen.findByText("docs");
    releaseSets();
    await new Promise((r) => setTimeout(r, 20));

    expect(screen.getByLabelText("Path")).toHaveValue("/home/u");
    expect(screen.getByText("docs")).toBeInTheDocument();
  });

  it("folder mode lists files only as context and returns the folder", async () => {
    const onSelect = vi.fn();
    render(<DirectoryPicker open onClose={() => {}} onSelect={onSelect} initialPath="/home/u/sets" showFiles />);

    expect(await screen.findByRole("button", { name: /a\.jsonl/ })).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(screen.getByRole("button", { name: "Select: sets" }));
    expect(onSelect).toHaveBeenCalledWith("/home/u/sets", { kind: "folder" });
  });

  it("the Hidden switch asks the server for hidden entries", async () => {
    render(<DirectoryPicker open onClose={() => {}} onSelect={() => {}} />);
    await screen.findByText("sets");
    const hidden = within(screen.getByText("Hidden").closest("label")).getByRole("checkbox");
    fireEvent.click(hidden);
    await waitFor(() => expect(requested().at(-1)).toMatchObject({ path: "/home/u", show_hidden: "true" }));
  });
});
