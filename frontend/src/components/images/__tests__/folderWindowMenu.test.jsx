import React from "react";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

vi.mock("axios", () => ({
  default: {
    get: vi.fn(async () => ({
      data: {
        data: {
          folders: [{ id: 1, name: "Picks", path: "Images/Trip/Picks" }],
          documents: [
            { id: 10, filename: "a-small.png", path: "Images/Trip/a-small.png", size: 10 },
            { id: 11, filename: "b-large.png", path: "Images/Trip/b-large.png", size: 900 },
          ],
        },
      },
    })),
  },
}));

import ImageThumbnailGrid from "../ImageThumbnailGrid";
import ImagesContextMenu from "../ImagesContextMenu";

const imageOrder = () => screen.getAllByRole("img").map((el) => el.getAttribute("alt"));

describe("right-click on empty space in a Media folder window", () => {
  it("opens the window's menu for the folder it shows, not a folder menu with nothing selected", async () => {
    const onContextMenu = vi.fn();
    const onSelectionChange = vi.fn();
    const { container } = render(
      <ImageThumbnailGrid
        currentPath="Images/Trip"
        onContextMenu={onContextMenu}
        onSelectionChange={onSelectionChange}
      />,
    );
    await screen.findByAltText("a-small.png");

    fireEvent.contextMenu(container.querySelector('[data-background="true"]'), { clientX: 5, clientY: 5 });

    expect(onContextMenu).toHaveBeenCalledTimes(1);
    const [, item, type] = onContextMenu.mock.calls[0];
    expect(type).toBe("folder-background");
    expect(item).toMatchObject({ path: "Images/Trip", name: "Trip" });

    expect(imageOrder()).toEqual(["a-small.png", "b-large.png"]);
    act(() => item.sortBy("size"));
    await waitFor(() => expect(imageOrder()).toEqual(["b-large.png", "a-small.png"]));

    item.selectAll();
    expect(onSelectionChange).toHaveBeenLastCalledWith(new Set(["folder-1", "file-11", "file-10"]));
  });

  it("offers New Folder, Paste into this folder, Select All and sorting, and no item actions", () => {
    const onPaste = vi.fn();
    const onSortBy = vi.fn();
    render(
      <ImagesContextMenu
        anchorPosition={{ top: 5, left: 5 }}
        onClose={() => {}}
        onPaste={onPaste}
        onSortBy={onSortBy}
        hasClipboard
        contextType="folder-background"
      />,
    );
    expect(screen.getAllByRole("menuitem").map((el) => el.textContent)).toEqual([
      "New Folder", "Paste into this folder", "Select All", "Sort by Name", "Sort by Date", "Sort by Size",
    ]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Paste into this folder" }));
    expect(onPaste).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("menuitem", { name: "Sort by Date" }));
    expect(onSortBy).toHaveBeenCalledWith("date");
  });
});
