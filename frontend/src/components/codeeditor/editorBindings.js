// frontend/src/components/codeeditor/editorBindings.js
// Monaco bindings for the Code Editor card. Monaco's onMount runs once per
// editor instance while tabs come and go underneath it, so every binding
// reads the active tab and its handlers through getters at the moment it
// fires, never from the closure that mounted the editor.

// Every tab gets its own Monaco model, keyed by this URI, so each keeps its
// own undo stack: with one shared model, Ctrl+Z right after a tab switch put
// the previous tab's text into the current one.
export function modelPathFor(tab) {
  if (!tab) return undefined;
  const name = (tab.filePath || "untitled").split("/").pop();
  return `inmemory://code-editor/${encodeURIComponent(String(tab.id))}/${encodeURIComponent(name)}`;
}

/** Dispose the models of tabs that are gone, so closed tabs do not pile up in Monaco. */
export function disposeTabModels(monaco, tabs) {
  if (!monaco?.editor?.getModel || !monaco.Uri?.parse) return 0;
  let disposed = 0;
  for (const tab of tabs || []) {
    const path = modelPathFor(tab);
    if (!path) continue;
    const model = monaco.editor.getModel(monaco.Uri.parse(path));
    if (model && !model.isDisposed?.()) {
      model.dispose();
      disposed += 1;
    }
  }
  return disposed;
}

const CHAT_ACTIONS = [
  { id: "chat.ask", label: "Ask Chat", action: "ask", order: 1 },
  { id: "chat.fix", label: "Fix This", action: "fix", order: 2 },
  { id: "chat.explain", label: "Explain", action: "explain", order: 3, shortcut: true },
  { id: "chat.addToChat", label: "Add to Chat", action: "add", order: 4 },
];

const selectedText = (ed) => {
  const sel = ed.getSelection();
  const model = ed.getModel();
  return sel && model ? model.getValueInRange(sel) : "";
};

/**
 * Register the card's editor bindings.
 * @param {object} editor Monaco editor instance
 * @param {object} monaco Monaco namespace
 * @param {object} handlers
 * @param {() => object|null} handlers.getTab the tab showing right now
 * @param {() => void} handlers.save save the tab showing right now
 * @param {(action: string, text: string, filePath: string|undefined) => void} [handlers.chatAction]
 */
export function bindEditorActions(editor, monaco, handlers) {
  const { getTab, save, chatAction } = handlers;
  const tabPath = () => getTab()?.filePath;

  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
    save();
  });

  // Chat actions in Monaco's right-click menu
  for (const item of CHAT_ACTIONS) {
    editor.addAction({
      id: item.id,
      label: item.label,
      contextMenuGroupId: "9_chat",
      contextMenuOrder: item.order,
      ...(item.shortcut ? { keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyE] } : {}),
      precondition: "editorHasSelection",
      run: (ed) => {
        const text = selectedText(ed);
        if (text && chatAction) chatAction(item.action, text, tabPath());
      },
    });
  }
}
