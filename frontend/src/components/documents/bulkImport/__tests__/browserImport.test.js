import { describe, it, expect, vi } from "vitest";
import {
  isSensitiveName,
  MAX_UPLOAD_BYTES,
  refusalKind,
  stageFiles,
  stagedLabel,
  uploadStaged,
} from "../browserImport";

const file = (name, size = 10) => ({ name, size });
const item = (relativePath, size) => ({ file: file(relativePath.split("/").pop(), size), relativePath });

describe("refusalKind", () => {
  it("follows the server import's rules", () => {
    expect(refusalKind("notes.txt")).toBeNull();
    expect(refusalKind("Contracts/sub/a.md")).toBeNull();
    expect(refusalKind("README")).toBeNull();
    expect(refusalKind(".env")).toBe("hidden_files");
    expect(refusalKind("Contracts/.git/config")).toBe("hidden_dirs");
    expect(refusalKind("id_rsa")).toBe("sensitive_files");
    expect(refusalKind("keys/server.PEM")).toBe("sensitive_files");
    expect(refusalKind("credentials.json")).toBe("sensitive_files");
  });

  it("matches the credential names case-blind", () => {
    expect(isSensitiveName("Client_Secret_1.JSON")).toBe(true);
    expect(isSensitiveName("secrets_guide.md")).toBe(false);
  });
});

describe("stageFiles", () => {
  it("counts a hidden folder once however many files it holds", () => {
    const staged = stageFiles([
      item("repo/a.py"),
      item("repo/.git/config"),
      item("repo/.git/HEAD"),
      item("repo/.git/objects/ab/cd"),
      item("repo/.env"),
    ]);
    expect(staged.files.map((f) => f.relativePath)).toEqual(["repo/a.py"]);
    expect(staged.skipped.hidden_dirs).toBe(1);
    expect(staged.skipped.hidden_files).toBe(1);
  });

  it("leaves out a file over the upload limit and says which", () => {
    const staged = stageFiles([item("big.mov", MAX_UPLOAD_BYTES + 1), item("small.txt", 5)]);
    expect(staged.files).toHaveLength(1);
    expect(staged.tooLarge).toEqual(["big.mov"]);
    expect(staged.totalBytes).toBe(5);
  });

  it("names a dropped folder, or counts loose files", () => {
    expect(stagedLabel([item("Contracts/a.pdf"), item("Contracts/b.pdf")])).toBe('"Contracts"');
    expect(stagedLabel([item("a.pdf"), item("b.pdf")])).toBe("2 files");
  });
});

describe("uploadStaged", () => {
  const httpWith = (existing = {}) => ({
    get: vi.fn(async (url, { params }) => {
      if (url.endsWith("/browse")) {
        return {
          data: {
            data: {
              folders: [],
              documents: (existing[params.path] || []).map((filename) => ({ filename })),
            },
          },
        };
      }
      throw new Error(`unexpected GET ${url}`);
    }),
    post: vi.fn(async (url) => {
      if (url.endsWith("/folder")) return { data: { data: { name: "sub", path: "Imports/sub" } } };
      return { data: { data: { filename: "x", job_id: "j" } } };
    }),
  });

  it("asks the server for the import rules on every upload and skips names already there", async () => {
    const http = httpWith({ Imports: ["old.txt"] });
    const result = await uploadStaged({
      files: [item("old.txt"), item("new.txt")],
      baseFolder: "Imports",
      http,
      filesApiBase: "/api/files",
    });
    expect(result).toMatchObject({ imported: 1, skipped: 1, errors: 0, queued_for_indexing: 1 });
    const uploads = http.post.mock.calls.filter(([url]) => url.endsWith("/upload"));
    expect(uploads).toHaveLength(1);
    const form = uploads[0][1];
    expect(form.get("import_rules")).toBe("bulk");
    expect(form.get("relative_path")).toBe("new.txt");
    expect(form.get("folder_path")).toBe("Imports");
  });

  it("keeps going after a failure and stops when asked", async () => {
    const http = httpWith();
    http.post.mockRejectedValueOnce({ response: { data: { error: { message: "Hidden files are not imported" } } } });
    let stop = false;
    const result = await uploadStaged({
      files: [item("a.txt"), item("b.txt"), item("c.txt")],
      baseFolder: "Imports",
      http,
      filesApiBase: "/api/files",
      onProgress: ({ done }) => {
        if (done === 1) stop = true;
      },
      shouldStop: () => stop,
    });
    expect(result.errors).toBe(1);
    expect(result.failures[0]).toBe("a.txt: Hidden files are not imported");
    expect(result.imported).toBe(1);
    expect(result.stopped).toBe(true);
  });
});
