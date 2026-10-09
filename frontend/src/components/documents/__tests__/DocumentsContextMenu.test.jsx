import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import DocumentsContextMenu from "../DocumentsContextMenu";

const open = { top: 10, left: 10 };

describe("DocumentsContextMenu", () => {
  it.each(["desktop", "folder-window", "folder", "file"])(
    "calls Paste with no arguments and closes, on a %s menu",
    (contextType) => {
      const onPaste = vi.fn();
      const onClose = vi.fn();
      render(
        <DocumentsContextMenu
          anchorPosition={open}
          onClose={onClose}
          onPaste={onPaste}
          hasClipboard
          contextType={contextType}
        />,
      );

      fireEvent.click(screen.getByRole("menuitem", { name: "Paste" }));

      expect(onPaste).toHaveBeenCalledTimes(1);
      expect(onPaste.mock.calls[0]).toEqual([]);
      expect(onClose).toHaveBeenCalledTimes(1);
    },
  );

  it("labels the open action as given", () => {
    render(
      <DocumentsContextMenu
        anchorPosition={open}
        onClose={() => {}}
        onPaste={() => {}}
        onOpenWindow={() => {}}
        openLabel="Open"
        contextType="folder"
      />,
    );

    expect(screen.getByRole("menuitem", { name: "Open" })).toBeInTheDocument();
  });
});
