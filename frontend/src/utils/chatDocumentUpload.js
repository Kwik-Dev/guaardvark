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

function uploadNotice({ file, documentId, indexed, codeGenMode }) {
  const fileType = file.name.split(".").pop().toLowerCase();
  const isCodeFile = CODE_EXTENSIONS.has("." + fileType);
  const fileSizeKB = (file.size / 1024).toFixed(1);
  const indexingStatus = indexed ? "Uploaded and indexed successfully" : "Uploaded (indexing in progress)";

  if (codeGenMode && isCodeFile && indexed) {
    return `/codegen

Please analyze and refactor the uploaded code file: ${file.name}

Requirements:
- Analyze the complete file content (${fileSizeKB} KB)
- Provide clean, refactored code only (no commentary)
- Maintain all functionality while improving code structure
- Fix any obvious issues or inefficiencies

Document ID: ${documentId}`;
  }
  if (codeGenMode && !indexed) {
    return `**File Upload Complete - Indexing in Progress**

Please wait for indexing to complete before processing. File: ${file.name} (${fileSizeKB} KB)`;
  }
  if (isCodeFile) {
    return `**Code File Uploaded Successfully**

**File Details:**
- **Name:** ${file.name}
- **Type:** ${fileType.toUpperCase()} (Code File)
- **Size:** ${fileSizeKB} KB
- **Document ID:** ${documentId || "N/A"}

**Status:** ${indexingStatus}
**Enhanced Analysis:** Code content is ${indexed ? "now" : "being"} indexed and ${indexed ? "available" : "will be available"} for search and discussion.

${indexed
    ? "You can ask questions about this code file and I'll analyze the complete content!"
    : "Please wait a moment for indexing to complete, then ask questions about the code."}`;
  }
  return `**Document Uploaded Successfully**

**File Details:**
- **Name:** ${file.name}
- **Type:** ${fileType.toUpperCase()}
- **Size:** ${fileSizeKB} KB
- **Document ID:** ${documentId || "N/A"}

**Status:** ${indexingStatus}
**RAG Integration:** The document is ${indexed ? "now" : "being"} indexed and ${indexed ? "available" : "will be available"} for search and context retrieval.`;
}

async function waitForIndexing(documentId, onStage) {
  for (let attempt = 1; attempt <= INDEX_WAIT_ATTEMPTS; attempt += 1) {
    try {
      const response = await fetch(`/api/docs/${documentId}`);
      if (response.ok) {
        const doc = await response.json();
        if (doc.index_status === "INDEXED" || doc.index_status === "STORED") return true;
        if (doc.index_status === "ERROR") {
          console.warn("Document indexing failed");
          return false;
        }
      }
    } catch (error) {
      console.warn("Error checking indexing status:", error);
    }
    onStage?.({ progress: 75 + (attempt / INDEX_WAIT_ATTEMPTS) * 20, indexing: true });
    await new Promise((resolve) => setTimeout(resolve, INDEX_WAIT_MS));
  }
  return false;
}

/**
 * Upload a chat document, tag it with the session, and wait (up to 30 s) for indexing.
 *
 * @param {File} file
 * @param {object} opts
 * @param {string} opts.sessionId      chat session the document is tagged with
 * @param {boolean} [opts.codeGenMode] send a /codegen request for code files
 * @param {function} [opts.onStage]    called with { progress, indexing } as it goes
 * @returns {Promise<{ok: boolean, message: string, error?: string}>} the notice to send
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
    const indexed = await waitForIndexing(result.document_id, onStage);
    onStage?.({ progress: 100, indexing: false });

    return {
      ok: true,
      message: uploadNotice({ file, documentId: result.document_id, indexed, codeGenMode }),
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
