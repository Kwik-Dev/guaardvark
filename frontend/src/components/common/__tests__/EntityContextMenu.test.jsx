import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import EntityContextMenu from "../EntityContextMenu";
import useContextMenu from "../../../hooks/useContextMenu";

function Owner({ onOwnerMenu, actions }) {
  const menu = useContextMenu();
  return (
    <div
      data-testid="owner"
      onContextMenu={(e) => {
        onOwnerMenu();
        menu.open(e);
      }}
    >
      owner
      <EntityContextMenu anchorPosition={menu.anchorPosition} onClose={menu.close} actions={actions} />
    </div>
  );
}

describe("EntityContextMenu", () => {
  it("closes on a right-click inside it instead of reopening its owner's menu", () => {
    const onOwnerMenu = vi.fn();
    render(<Owner onOwnerMenu={onOwnerMenu} actions={[{ label: "Open", onClick: () => {} }]} />);
    fireEvent.contextMenu(screen.getByTestId("owner"), { clientX: 10, clientY: 10 });
    expect(onOwnerMenu).toHaveBeenCalledTimes(1);

    const notCancelled = fireEvent.contextMenu(screen.getByRole("menuitem", { name: "Open" }), {
      clientX: 12,
      clientY: 12,
    });
    expect(notCancelled).toBe(false);
    expect(onOwnerMenu).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("gives every item the icon column once one item has an icon or a tick", () => {
    render(
      <EntityContextMenu
        anchorPosition={{ top: 5, left: 5 }}
        onClose={() => {}}
        actions={[
          { label: "Refresh", onClick: () => {} },
          { label: "Speed tier", checked: true, onClick: () => {} },
        ]}
      />,
    );
    for (const item of screen.getAllByRole("menuitem")) {
      expect(item.querySelector(".MuiListItemIcon-root")).not.toBeNull();
    }
  });

  it("renders plain labels when no item has an icon", () => {
    render(
      <EntityContextMenu
        anchorPosition={{ top: 5, left: 5 }}
        onClose={() => {}}
        actions={[{ label: "Open", onClick: () => {} }, { label: "Delete", onClick: () => {} }]}
      />,
    );
    for (const item of screen.getAllByRole("menuitem")) {
      expect(item.querySelector(".MuiListItemIcon-root")).toBeNull();
    }
  });
});
