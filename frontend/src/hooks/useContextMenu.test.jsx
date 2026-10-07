import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import useContextMenu from "./useContextMenu";
import EntityContextMenu from "../components/common/EntityContextMenu";

function Harness({ onPick = () => {} }) {
  const menu = useContextMenu();
  return (
    <div>
      <div data-testid="row" onContextMenu={(e) => menu.open(e, { id: 7 })}>
        row <input aria-label="field" /> <a href="/x">link</a>
        <span data-native-contextmenu>editor</span>
      </div>
      <EntityContextMenu
        anchorPosition={menu.anchorPosition}
        onClose={menu.close}
        actions={
          menu.payload
            ? [
                { label: `Open ${menu.payload.id}`, onClick: () => onPick(menu.payload.id) },
                false,
                { label: "Fast", checked: true, onClick: () => {} },
                { label: "Slow", checked: false, onClick: () => {} },
              ]
            : []
        }
      />
    </div>
  );
}

describe("useContextMenu with EntityContextMenu", () => {
  it("opens at the pointer with the row's payload and skips falsy actions", () => {
    const picked = [];
    render(<Harness onPick={(id) => picked.push(id)} />);
    fireEvent.contextMenu(screen.getByTestId("row"), { clientX: 40, clientY: 50 });
    expect(screen.getByRole("menuitem", { name: "Open 7" })).toBeInTheDocument();
    expect(screen.getAllByRole("menuitem")).toHaveLength(3);
    fireEvent.click(screen.getByRole("menuitem", { name: "Open 7" }));
    expect(picked).toEqual([7]);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("marks the checked choice", () => {
    render(<Harness />);
    fireEvent.contextMenu(screen.getByTestId("row"), { clientX: 10, clientY: 10 });
    expect(screen.getByRole("menuitem", { name: "Fast" })).toHaveClass("Mui-selected");
    expect(screen.getByRole("menuitem", { name: "Slow" })).not.toHaveClass("Mui-selected");
  });

  it("leaves the browser menu on fields, links and marked editors", () => {
    render(<Harness />);
    for (const el of [screen.getByLabelText("field"), screen.getByText("link"), screen.getByText("editor")]) {
      const notCancelled = fireEvent.contextMenu(el, { clientX: 5, clientY: 5 });
      expect(notCancelled).toBe(true);
    }
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("anchors to the element when the menu key fires at 0,0", () => {
    render(<Harness />);
    fireEvent.contextMenu(screen.getByTestId("row"), { clientX: 0, clientY: 0 });
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });
});
