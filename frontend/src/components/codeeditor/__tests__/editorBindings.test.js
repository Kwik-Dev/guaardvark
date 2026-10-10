import { describe, it, expect, vi } from "vitest";
import { bindEditorActions, modelPathFor, disposeTabModels } from "../editorBindings";

describe("per-tab models", () => {
  it("gives two tabs showing the same file name different model paths", () => {
    const a = modelPathFor({ id: "a1", filePath: "app.py" });
    const b = modelPathFor({ id: "b2", filePath: "other/app.py" });
    expect(a).not.toBe(b);
    expect(a).toMatch(/^inmemory:\/\/code-editor\/a1\/app\.py$/);
    expect(modelPathFor({ id: "n", filePath: null })).toMatch(/\/untitled$/);
    expect(modelPathFor(null)).toBeUndefined();
  });

  it("disposes the models of closed tabs and leaves the others alone", () => {
    const models = new Map();
    const monaco = {
      Uri: { parse: (s) => s },
      editor: { getModel: (uri) => models.get(uri) || null },
    };
    const tabs = [{ id: "a", filePath: "a.js" }, { id: "b", filePath: "b.js" }];
    for (const tab of tabs) {
      const model = { disposed: false, isDisposed: () => model.disposed, dispose: () => { model.disposed = true; } };
      models.set(modelPathFor(tab), model);
    }
    expect(disposeTabModels(monaco, [tabs[0], { id: "never-opened", filePath: "x" }])).toBe(1);
    expect(models.get(modelPathFor(tabs[0])).disposed).toBe(true);
    expect(models.get(modelPathFor(tabs[1])).disposed).toBe(false);
    expect(disposeTabModels(monaco, [tabs[0]])).toBe(0);
    expect(disposeTabModels(null, tabs)).toBe(0);
  });
});

const fakeMonaco = () => ({
  KeyMod: { CtrlCmd: 2048, Shift: 1024 },
  KeyCode: { KeyS: 49, KeyE: 35 },
  Range: class {},
  languages: { registerHoverProvider: vi.fn(), registerCodeActionProvider: vi.fn() },
});

const fakeEditor = () => {
  const commands = [];
  const actions = {};
  return {
    commands,
    actions,
    addCommand: (keys, run) => commands.push({ keys, run }),
    addAction: (action) => { actions[action.id] = action; },
  };
};

const editorShowing = (text) => ({
  getSelection: () => ({ startLineNumber: 1 }),
  getModel: () => ({ getValueInRange: () => text }),
});

describe("bindEditorActions", () => {
  it("binds Ctrl+S once and saves whichever tab is active when it fires", () => {
    const editor = fakeEditor();
    const monaco = fakeMonaco();
    let tab = { id: "a", filePath: "first.js", language: "javascript" };
    const saved = [];
    bindEditorActions(editor, monaco, {
      getTab: () => tab,
      save: () => saved.push(tab.filePath),
    });

    expect(editor.commands).toHaveLength(1);
    expect(editor.commands[0].keys).toBe(2048 | 49);

    editor.commands[0].run();
    tab = { id: "b", filePath: "second.py", language: "python" };
    editor.commands[0].run();

    expect(saved).toEqual(["first.js", "second.py"]);
  });

  it("sends right-click chat actions with the active tab's path, not the mount-time tab", () => {
    const editor = fakeEditor();
    const monaco = fakeMonaco();
    let tab = { id: "a", filePath: "first.js", language: "javascript" };
    const chatAction = vi.fn();
    bindEditorActions(editor, monaco, { getTab: () => tab, save: () => {}, chatAction });

    expect(Object.keys(editor.actions).sort()).toEqual(["chat.addToChat", "chat.ask", "chat.explain", "chat.fix"]);

    tab = { id: "b", filePath: "second.py", language: "python" };
    editor.actions["chat.fix"].run(editorShowing("x = 1"));
    expect(chatAction).toHaveBeenCalledWith("fix", "x = 1", "second.py");

    editor.actions["chat.ask"].run(editorShowing(""));
    expect(chatAction).toHaveBeenCalledTimes(1);
  });
});
