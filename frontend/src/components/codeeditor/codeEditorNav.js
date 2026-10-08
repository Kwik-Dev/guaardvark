// Keyboard and jump-to-line helpers for the Code Editor page.

/** Ctrl/Cmd + digit jumps to a card, by layout id. */
export const CARD_SHORTCUTS = {
  1: "filetree",
  2: "editor",
  3: "chat",
  4: "search",
  5: "output",
};

const hasCtrl = (event) => Boolean(event?.ctrlKey || event?.metaKey);

/** The card id a Ctrl/Cmd+1..5 press asks for, or null. */
export function cardForShortcut(event) {
  if (!hasCtrl(event) || event.shiftKey || event.altKey) return null;
  return CARD_SHORTCUTS[event.key] || null;
}

/** True for Ctrl/Cmd+Shift+S. With Shift held the key arrives as "S". */
export function isSaveSessionShortcut(event) {
  return hasCtrl(event) && Boolean(event.shiftKey) && !event.altKey && String(event.key).toLowerCase() === "s";
}

/** True for Ctrl/Cmd+Shift+O (Find Symbol). */
export function isFindSymbolShortcut(event) {
  return hasCtrl(event) && Boolean(event.shiftKey) && !event.altKey && String(event.key).toLowerCase() === "o";
}

/** The grid item that holds a card, found by the data-card-id its wrapper carries. */
export function findCardElement(root, cardId) {
  if (!root || !cardId) return null;
  return root.querySelector(`[data-card-id="${cardId}"]`);
}

/**
 * Scroll a Monaco editor to a 1-based line, put the cursor there and focus it.
 * The line is clamped to the file. Returns false when the editor has no model yet.
 */
export function revealEditorLine(editor, line) {
  const model = editor?.getModel?.();
  if (!model) return false;
  const wanted = Number.parseInt(line, 10);
  const target = Math.min(Math.max(1, Number.isFinite(wanted) ? wanted : 1), model.getLineCount());
  editor.revealLineInCenter(target);
  editor.setPosition({ lineNumber: target, column: 1 });
  editor.focus();
  return true;
}

/** Index of an open tab already showing this document, or -1. */
export function findDocumentTab(tabs, doc) {
  if (!Array.isArray(tabs) || !doc) return -1;
  return tabs.findIndex(
    (tab) => (doc.id != null && tab.documentId === doc.id) || (doc.filePath && tab.filePath === doc.filePath),
  );
}
