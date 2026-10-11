import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

import ImagesContextMenu from "../ImagesContextMenu";

const menuItems = () => screen.getAllByRole("menuitem").map((el) => el.textContent);

describe("Media Library desktop menu", () => {
  it("offers Arrange Windows only while a folder window is open", () => {
    const { unmount } = render(
      <ImagesContextMenu anchorPosition={{ top: 5, left: 5 }} onClose={() => {}} contextType="desktop" />,
    );
    expect(menuItems()).toEqual([
      "New Folder", "Select All", "Sort by Name", "Sort by Date", "Sort by Size", "Arrange Icons",
    ]);
    unmount();

    const onArrangeWindows = vi.fn();
    render(
      <ImagesContextMenu
        anchorPosition={{ top: 5, left: 5 }}
        onClose={() => {}}
        onArrangeWindows={onArrangeWindows}
        contextType="desktop"
        hasOpenWindows
      />,
    );
    expect(menuItems()).toContain("Arrange Windows");
    fireEvent.click(screen.getByRole("menuitem", { name: "Arrange Windows" }));
    expect(onArrangeWindows).toHaveBeenCalled();
  });
});

describe("Media Library folder menu", () => {
  it("names the colour swatches in plain words and still stores the hex", () => {
    const onColorChange = vi.fn();
    render(
      <ImagesContextMenu
        anchorPosition={{ top: 5, left: 5 }}
        onClose={() => {}}
        onColorChange={onColorChange}
        contextType="folder"
        folderColor="#A5D6A7"
      />,
    );
    for (const name of ["Blue", "Green", "Orange", "Red", "Purple", "Cyan", "Coral", "Grey"]) {
      expect(screen.getByLabelText(name)).toBeTruthy();
    }
    expect(screen.queryByLabelText("#90CAF9")).toBeNull();

    fireEvent.click(screen.getByLabelText("Blue"));
    expect(onColorChange).toHaveBeenCalledWith("#90CAF9");
  });
});
