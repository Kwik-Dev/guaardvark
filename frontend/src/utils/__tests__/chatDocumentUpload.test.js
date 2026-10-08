import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const { uploadFile } = vi.hoisted(() => ({ uploadFile: vi.fn() }));
vi.mock("../../api", () => ({ uploadFile }));

import { indexOutcome, uploadChatDocument } from "../chatDocumentUpload";

// What GET /api/docs/<id> answers on each poll.
const docStatus = (...rows) => {
  let i = 0;
  global.fetch = vi.fn(async () => {
    const row = rows[Math.min(i, rows.length - 1)];
    i += 1;
    return { ok: true, json: async () => row };
  });
};

const pdf = () => new File(["%PDF-1.4"], "report.pdf", { type: "application/pdf" });
const notes = () => new File(["hello"], "notes.md", { type: "text/markdown" });

beforeEach(() => {
  uploadFile.mockReset();
  uploadFile.mockResolvedValue({ document_id: 42 });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("indexOutcome", () => {
  it("reads the document row the indexer writes", () => {
    expect(indexOutcome({ index_status: "INDEXED" })).toEqual({ outcome: "indexed" });
    expect(indexOutcome({ index_status: "STORED" })).toEqual({ outcome: "stored" });
    expect(indexOutcome({ index_status: "PENDING" })).toEqual({ outcome: "queued" });
    expect(indexOutcome({ index_status: "PENDING", error_message: "Waiting for the vector store: embeddings offline" }))
      .toEqual({ outcome: "deferred", reason: "Waiting for the vector store: embeddings offline" });
    expect(indexOutcome({ index_status: "ERROR", error_message: "boom" })).toEqual({ outcome: "failed", reason: "boom" });
  });
});

describe("the notice a chat upload sends", () => {
  it("says indexed only when the document is indexed", async () => {
    docStatus({ index_status: "PENDING" }, { index_status: "INDEXED" });
    vi.useFakeTimers();
    const pending = uploadChatDocument(pdf(), { sessionId: "s1" });
    await vi.runAllTimersAsync();
    const result = await pending;
    expect(result.indexing).toBe("indexed");
    expect(result.message).toContain("**Status:** Uploaded and indexed");
  });

  it("says indexing is queued for a stored text file, not that it was indexed", async () => {
    docStatus({ index_status: "STORED" });
    const result = await uploadChatDocument(notes(), { sessionId: "s1" });
    expect(result.indexing).toBe("stored");
    expect(result.message).toContain("**Status:** Uploaded; indexing queued");
    expect(result.message).toContain("full text is available to this chat now");
    expect(result.message).not.toMatch(/indexed successfully|Uploaded and indexed/);
  });

  it("says indexing is queued, and why, when the indexer deferred it", async () => {
    docStatus({ index_status: "PENDING", error_message: "Waiting for the vector store: embeddings offline" });
    const result = await uploadChatDocument(pdf(), { sessionId: "s1" });
    expect(result.indexing).toBe("deferred");
    expect(result.message).toContain(
      "**Status:** Uploaded; indexing queued (Waiting for the vector store: embeddings offline)",
    );
  });

  it("says indexing is still queued when it has not finished within the wait", async () => {
    docStatus({ index_status: "PENDING" });
    vi.useFakeTimers();
    const pending = uploadChatDocument(pdf(), { sessionId: "s1" });
    await vi.runAllTimersAsync();
    const result = await pending;
    expect(result.indexing).toBe("queued");
    expect(result.message).toContain("**Status:** Uploaded; indexing queued");
  });

  it("says indexing failed when the indexer marked it an error", async () => {
    docStatus({ index_status: "ERROR", error_message: "parser crashed" });
    const result = await uploadChatDocument(pdf(), { sessionId: "s1" });
    expect(result.message).toContain("**Status:** Uploaded; indexing failed (parser crashed)");
    expect(result.message).toContain("re-indexed from Files");
  });
});
