// frontend/src/components/codeeditor/tabOrigin.js
// Where an editor tab came from decides where a save, rename or delete goes.
// The origin is the `source` tag set when the tab is opened (FileTreeCard,
// CodeEditorPage, WordPressPagesCard); it is never inferred from the file name,
// because a library document and a repo file can share one.

export const TAB_SOURCE = {
  // Files kept in the browser's localStorage (new tabs land here).
  LOCAL: "local",
  // A document in the library (Files page), addressed by its document id.
  DOCUMENT: "document",
  // A file under the repo root, addressed by its repo-relative path.
  FILESYSTEM: "filesystem",
  // A read-only view of the running checkout.
  LIVE_REPO: "live_repo",
  WORDPRESS: "wordpress",
};

/**
 * Resolve the save target of a tab.
 * @returns {{kind: "none"|"readonly"|"error"|"document"|"repo"|"local", documentId?: number, path?: string|null, message?: string}}
 */
export function saveTargetFor(tab) {
  if (!tab) return { kind: "none" };
  if (tab.readOnly || tab.source === TAB_SOURCE.LIVE_REPO) {
    return {
      kind: "readonly",
      message: "Live repository files are read-only here. Use self-code proposed edits for changes.",
    };
  }
  if (tab.source === TAB_SOURCE.DOCUMENT) {
    if (tab.documentId === null || tab.documentId === undefined || tab.documentId === "") {
      return {
        kind: "error",
        message: "This library document has no id, so it cannot be saved. Reopen it from the Files card.",
      };
    }
    return { kind: "document", documentId: tab.documentId };
  }
  if (tab.source === TAB_SOURCE.FILESYSTEM) {
    if (!tab.filePath || !String(tab.filePath).trim()) {
      return { kind: "error", message: "This repo file has no path, so it cannot be saved." };
    }
    return { kind: "repo", path: tab.filePath };
  }
  return { kind: "local", path: tab.filePath || null };
}
