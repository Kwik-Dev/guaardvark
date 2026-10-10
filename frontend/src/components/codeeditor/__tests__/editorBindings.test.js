import { describe, it, expect, vi } from "vitest";
import { bindEditorActions } from "../editorBindings";

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
