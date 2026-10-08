// frontend/src/utils/chatDocumentUpload.js
// The one path a document takes into a chat, whether it came from the
// paperclip or was dropped: upload tagged with the chat session, wait for
// indexing, then the notice the chat sends on the user's behalf.

import * as apiService from "../api";

/** Extensions the chat accepts as documents (images are handled separately). */
export const CHAT_DOCUMENT_TYPES = {
  ".py": "Python",
  ".js": "JavaScript",
  ".jsx": "React JSX",
  ".ts": "TypeScript",
  ".tsx": "TypeScript React",
  ".html": "HTML",
  ".css": "CSS",
  ".scss": "SCSS",
  ".json": "JSON",
  ".xml": "XML",
  ".yaml": "YAML",
  ".yml": "YAML",
  ".toml": "TOML",
  ".ini": "INI Config",
  ".conf": "Config",
  ".env": "Environment",
  ".csv": "CSV Data",
  ".xlsx": "Excel",
  ".xls": "Excel",
  ".pdf": "PDF Document",
  ".docx": "Word Document",
  ".txt": "Text File",
  ".md": "Markdown",
  ".rst": "ReStructuredText",
  ".sql": "SQL",
  ".sh": "Shell Script",
  ".bat": "Batch File",
  ".ps1": "PowerShell",
  ".dockerfile": "Dockerfile",
  ".gitignore": "Git Ignore",
  ".gitattributes": "Git Attributes",
};

/** The file picker's accept list for the chat paperclip. */
export const CHAT_ATTACH_ACCEPT =
  ".pdf,.txt,.csv,.docx,.md,.json,.py,.js,.jsx,.ts,.tsx,.html,.css,.xml,.yaml,.yml,image/*";

export const CHAT_DOCUMENT_MAX_BYTES = 100 * 1024 * 1024;

const CODE_EXTENSIONS = new Set([
  ".js", ".jsx", ".ts", ".tsx", ".py", ".java", ".cpp", ".c", ".h", ".hpp",
  ".cs", ".php", ".rb", ".go", ".rs", ".swift", ".kt", ".scala", ".sh",
  ".bash", ".sql", ".css", ".scss", ".sass", ".html", ".htm", ".xml",
  ".json", ".yaml", ".yml", ".vue", ".svelte", ".dart", ".r", ".lua",
]);

const INDEX_WAIT_ATTEMPTS = 30;
const INDEX_WAIT_MS = 1000;

const extensionOf = (name) => "." + String(name || "").split(".").pop().toLowerCase();

/** True when the chat should treat the file as an image attachment. */
export const isChatImage = (file) => Boolean(file?.type && file.type.startsWith("image/"));

/** Split dropped or picked files into image attachments and documents. */
export function splitChatFiles(files) {
  const list = Array.from(files || []).filter(Boolean);
  return {
    images: list.filter(isChatImage),
    documents: list.filter((f) => !isChatImage(f)),
  };
}

/**
 * The notice sent instead of an upload when the file cannot be taken, or null.
 * @param {File} file
 * @returns {string|null}
 */
export function chatDocumentRefusal(file) {
  if (file.size > CHAT_DOCUMENT_MAX_BYTES) {
    return `**File Too Large**

**File:** ${file.name}
**Size:** ${(file.size / 1024 / 1024).toFixed(1)} MB
**Limit:** 100 MB

Please select a smaller file or compress the file before uploading.`;
  }
  const extension = extensionOf(file.name);
  const supported = CHAT_DOCUMENT_TYPES[extension] || (file.type || "").startsWith("text/");
  if (!supported) {
    return `**Unsupported File Type**

**File:** ${file.name}
**Type:** ${extension}
**Supported Types:** ${Object.keys(CHAT_DOCUMENT_TYPES).join(", ")}, images

Please select a supported file type.`;
  }
  return null;
}

/** The Status line and what the document is good for now, per indexing outcome. */
function indexingLines({ outcome, reason }) {
  switch (outcome) {
    case "indexed":
      return { status: "Uploaded and indexed", now: "It is available for search and context retrieval now." };
    case "stored":
      return {
        status: "Uploaded; indexing queued",
        now: "Its full text is available to this chat now; it becomes searchable once indexing finishes.",
      };
    case "deferred":
      return {
        status: `Uploaded; indexing queued${reason ? ` (${reason})` : ""}`,
        now: "It becomes searchable once indexing runs.",
      };
    case "failed":
      return {
        status: `Uploaded; indexing failed${reason ? ` (${reason})` : ""}`,
        now: "It is not searchable until it is re-indexed from Files.",
      };
    default:
      return { status: "Uploaded; indexing queued", now: "It becomes searchable once indexing finishes." };
  }
}

function uploadNotice({ file, documentId, indexing, codeGenMode }) {
  const fileType = file.name.split(".").pop().toLowerCase();
  const isCodeFile = CODE_EXTENSIONS.has("." + fileType);
  const fileSizeKB = (file.size / 1024).toFixed(1);
  // The chat reads a document's stored text, so a stored file is ready to discuss.
  const readable = indexing.outcome === "indexed" || indexing.outcome === "stored";
  const lines = indexingLines(indexing);

  if (codeGenMode && isCodeFile && readable) {
    return `/codegen

Please analyze and refactor the uploaded code file: ${file.name}

Requirements:
- Analyze the complete file content (${fileSizeKB} KB)
- Provide clean, refactored code only (no commentary)
- Maintain all functionality while improving code structure
- Fix any obvious issues or inefficiencies

Document ID: ${documentId}`;
  }
  if (codeGenMode && !readable) {
    return `**File Uploaded - Not Ready Yet**

**Status:** ${lines.status}
Please wait for indexing to finish before processing. File: ${file.name} (${fileSizeKB} KB)`;
  }
  if (isCodeFile) {
    return `**Code File Uploaded Successfully**

**File Details:**
- **Name:** ${file.name}
- **Type:** ${fileType.toUpperCase()} (Code File)
- **Size:** ${fileSizeKB} KB
- **Document ID:** ${documentId || "N/A"}

**Status:** ${lines.status}
${lines.now}

${readable
    ? "You can ask questions about this code file and I'll analyze the complete content."
    : "Ask about the code once indexing has finished."}`;
  }
  return `**Document Uploaded Successfully**

**File Details:**
- **Name:** ${file.name}
- **Type:** ${fileType.toUpperCase()}
- **Size:** ${fileSizeKB} KB
- **Document ID:** ${documentId || "N/A"}

**Status:** ${lines.status}
${lines.now}`;
}

// The indexer leaves a document PENDING with this message when the vector store
// is not in use; the resume task indexes it later (celery_tasks_isolated.py).
const DEFERRED_PREFIX = "Waiting for the vector store";

/**
 * Where indexing stands for a document, from its row.
 *
 * `stored`: a text or code file whose full text is saved and readable by the chat,
 * with search indexing still to run. `deferred`: indexing is parked until the
 * vector store is back. Anything not finished yet is `queued`.
 *
 * @param {{index_status?: string, error_message?: string}} doc
 * @returns {{outcome: "indexed"|"stored"|"deferred"|"failed"|"queued", reason?: string}}
 */
export function indexOutcome(doc) {
  const status = String(doc?.index_status || "").toUpperCase();
  const reason = doc?.error_message || undefined;
  if (status === "INDEXED") return { outcome: "indexed" };
  if (status === "STORED" || status === "STORED_TRUNCATED") return { outcome: "stored" };
  if (status === "ERROR") return { outcome: "failed", reason };
  if (status === "PENDING" && reason?.startsWith(DEFERRED_PREFIX)) return { outcome: "deferred", reason };
  return { outcome: "queued" };
}

async function waitForIndexing(documentId, onStage) {
  for (let attempt = 1; attempt <= INDEX_WAIT_ATTEMPTS; attempt += 1) {
    try {
      const response = await fetch(`/api/docs/${documentId}`);
      if (response.ok) {
        const state = indexOutcome(await response.json());
        if (state.outcome !== "queued") return state;
      }
    } catch (error) {
      console.warn("Error checking indexing status:", error);
    }
    onStage?.({ progress: 75 + (attempt / INDEX_WAIT_ATTEMPTS) * 20, indexing: true });
    await new Promise((resolve) => setTimeout(resolve, INDEX_WAIT_MS));
  }
  return { outcome: "queued" };
}

/**
 * Upload a chat document, tag it with the session, and wait (up to 30 s) for indexing.
 *
 * @param {File} file
 * @param {object} opts
 * @param {string} opts.sessionId      chat session the document is tagged with
 * @param {boolean} [opts.codeGenMode] send a /codegen request for code files
 * @param {function} [opts.onStage]    called with { progress, indexing } as it goes
 * @returns {Promise<{ok: boolean, message: string, indexing?: string, error?: string}>}
 *   the notice to send, and the indexing outcome it reports (see indexOutcome)
 */
export async function uploadChatDocument(file, { sessionId, codeGenMode = false, onStage } = {}) {
  try {
    const result = await apiService.uploadFile(
      file,
      null,
      `chat-upload,file-upload,${sessionId}`,
      {},
      null,
      (p) => onStage?.({ progress: p.percentage, indexing: false }),
    );
    if (result?.error) throw new Error(result.error);

    onStage?.({ progress: 75, indexing: true });
    const indexing = await waitForIndexing(result.document_id, onStage);
    onStage?.({ progress: 100, indexing: false });

    return {
      ok: true,
      indexing: indexing.outcome,
      message: uploadNotice({ file, documentId: result.document_id, indexing, codeGenMode }),
    };
  } catch (error) {
    console.error("File upload failed:", error);
    return {
      ok: false,
      error: error.message,
      message: `**File Upload Failed**

**File:** ${file.name}
**Error:** ${error.message}

Please try uploading the file again or contact support if the issue persists.`,
    };
  }
}
