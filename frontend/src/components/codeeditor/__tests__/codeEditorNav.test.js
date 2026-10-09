// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render } from "@testing-library/react";
import ReactGridLayout from "react-grid-layout/legacy";
import {
  cardForShortcut,
  findCardElement,
  findDocumentTab,
  isFindSymbolShortcut,
  isSaveSessionShortcut,
  revealEditorLine,
} from "../codeEditorNav";

const key = (k, mods = {}) => ({ key: k, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...mods });

describe("card shortcuts", () => {
  it("maps Ctrl or Cmd + 1..5 to the five cards", () => {
    expect(["1", "2", "3", "4", "5"].map((k) => cardForShortcut(key(k, { ctrlKey: true })))).toEqual([
      "filetree", "editor", "chat", "search", "output",
    ]);
    expect(cardForShortcut(key("3", { metaKey: true }))).toBe("chat");
  });

  it("ignores digits without Ctrl, with Shift or Alt, and digits past 5", () => {
    expect(cardForShortcut(key("1"))).toBe(null);
    expect(cardForShortcut(key("1", { ctrlKey: true, shiftKey: true }))).toBe(null);
    expect(cardForShortcut(key("1", { ctrlKey: true, altKey: true }))).toBe(null);
    expect(cardForShortcut(key("6", { ctrlKey: true }))).toBe(null);
  });

  it("finds a card the grid rendered, by the data-card-id its wrapper keeps", () => {
    const layout = [{ i: "filetree", x: 0, y: 0, w: 2, h: 2 }, { i: "chat", x: 2, y: 0, w: 2, h: 2 }];
    const { container } = render(
      React.createElement(
        ReactGridLayout,
        { layout, cols: 4, rowHeight: 10, width: 400 },
        layout.map((item) => React.createElement("div", { key: item.i, "data-grid": item, "data-card-id": item.i })),
      ),
    );
    const chat = findCardElement(container, "chat");
    expect(chat).not.toBe(null);
    expect(chat.classList.contains("react-grid-item")).toBe(true);
    expect(findCardElement(container, "output")).toBe(null);
  });
});

describe("Ctrl+Shift shortcuts", () => {
  it("saves the session on Ctrl+Shift+S, where the key arrives as a capital S", () => {
    expect(isSaveSessionShortcut(key("S", { ctrlKey: true, shiftKey: true }))).toBe(true);
    expect(isSaveSessionShortcut(key("s", { metaKey: true, shiftKey: true }))).toBe(true);
    expect(isSaveSessionShortcut(key("s", { ctrlKey: true }))).toBe(false);
  });

  it("opens Find Symbol on Ctrl+Shift+O", () => {
    expect(isFindSymbolShortcut(key("O", { ctrlKey: true, shiftKey: true }))).toBe(true);
    expect(isFindSymbolShortcut(key("o", { ctrlKey: true }))).toBe(false);
  });
});

describe("jumping to a symbol", () => {
  const fakeEditor = (lines) => ({
    getModel: () => ({ getLineCount: () => lines }),
    revealLineInCenter: vi.fn(),
    setPosition: vi.fn(),
    focus: vi.fn(),
  });

  it("moves the cursor to the symbol's line and focuses the editor", () => {
    const editor = fakeEditor(200);
    expect(revealEditorLine(editor, 42)).toBe(true);
    expect(editor.revealLineInCenter).toHaveBeenCalledWith(42);
    expect(editor.setPosition).toHaveBeenCalledWith({ lineNumber: 42, column: 1 });
    expect(editor.focus).toHaveBeenCalled();
  });

  it("keeps the line inside the file and waits for an editor with a model", () => {
    const editor = fakeEditor(10);
    revealEditorLine(editor, 99);
    expect(editor.revealLineInCenter).toHaveBeenCalledWith(10);
    expect(revealEditorLine(null, 5)).toBe(false);
    expect(revealEditorLine({ getModel: () => null }, 5)).toBe(false);
  });

  it("reuses the tab already showing the document", () => {
    const tabs = [{ id: "a", documentId: 7, filePath: "src/a.py" }, { id: "b", documentId: 9, filePath: "src/b.py" }];
    expect(findDocumentTab(tabs, { id: 9 })).toBe(1);
    expect(findDocumentTab(tabs, { id: 3, filePath: "src/a.py" })).toBe(0);
    expect(findDocumentTab(tabs, { id: 3, filePath: "src/c.py" })).toBe(-1);
  });
});
