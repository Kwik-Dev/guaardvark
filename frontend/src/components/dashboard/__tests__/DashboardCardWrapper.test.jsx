import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

import DashboardCardWrapper from "../DashboardCardWrapper";

const header = (container) => container.querySelector(".card-header-buttons");

describe("DashboardCardWrapper right-click menu", () => {
  beforeEach(() => navigate.mockClear());

  it("leaves the browser menu alone when contextMenuActions is not passed", () => {
    render(
      <DashboardCardWrapper title="Notes" onToggleMinimize={() => {}} onCardColorChange={() => {}}>
        <div>body</div>
      </DashboardCardWrapper>,
    );
    const notCancelled = fireEvent.contextMenu(screen.getByText("body"), { clientX: 20, clientY: 20 });
    expect(notCancelled).toBe(true);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("lists the generic items, then the card's own, and runs them", () => {
    const onHideCard = vi.fn();
    const onCardColorChange = vi.fn();
    const refresh = vi.fn();
    render(
      <DashboardCardWrapper
        id="gpu"
        title="GPU Memory"
        cardColor="#123456"
        onCardColorChange={onCardColorChange}
        onToggleMinimize={() => {}}
        onHideCard={onHideCard}
        contextMenuActions={[{ label: "Refresh", onClick: refresh }, false]}
      >
        <div>body</div>
      </DashboardCardWrapper>,
    );

    fireEvent.contextMenu(screen.getByText("body"), { clientX: 20, clientY: 20 });
    expect(screen.getAllByRole("menuitem").map((el) => el.textContent)).toEqual([
      "Open page",
      "Minimize",
      "Change colour…",
      "Reset colour",
      "Hide card",
      "Refresh",
    ]);

    fireEvent.click(screen.getByRole("menuitem", { name: "Refresh" }));
    expect(refresh).toHaveBeenCalledTimes(1);

    fireEvent.contextMenu(screen.getByText("body"), { clientX: 20, clientY: 20 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Reset colour" }));
    expect(onCardColorChange).toHaveBeenCalledWith(null);

    fireEvent.contextMenu(screen.getByText("body"), { clientX: 20, clientY: 20 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Hide card" }));
    expect(onHideCard).toHaveBeenCalledTimes(1);

    fireEvent.contextMenu(screen.getByText("body"), { clientX: 20, clientY: 20 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Open page" }));
    expect(navigate).toHaveBeenLastCalledWith("/plugins");
  });

  it("offers Expand on a minimized card and no Reset colour without a colour", () => {
    render(
      <DashboardCardWrapper
        id="autoresearch"
        title="RAG Autoresearch"
        isMinimized
        onCardColorChange={() => {}}
        onToggleMinimize={() => {}}
        contextMenuActions={[]}
      >
        <div>body</div>
      </DashboardCardWrapper>,
    );
    fireEvent.contextMenu(screen.getByText("RAG Autoresearch"), { clientX: 20, clientY: 20 });
    const labels = screen.getAllByRole("menuitem").map((el) => el.textContent);
    expect(labels).toContain("Expand");
    expect(labels).not.toContain("Reset colour");
    expect(labels).not.toContain("Hide card");
    fireEvent.click(screen.getByRole("menuitem", { name: "Open page" }));
    expect(navigate).toHaveBeenLastCalledWith("/autoresearch");
  });

  it("limits the menu to the title bar with contextMenuArea=header", () => {
    const { container } = render(
      <DashboardCardWrapper id="files" title="File Manager" contextMenuActions={[]} contextMenuArea="header">
        <div>body</div>
      </DashboardCardWrapper>,
    );
    expect(fireEvent.contextMenu(screen.getByText("body"), { clientX: 20, clientY: 20 })).toBe(true);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.contextMenu(header(container), { clientX: 20, clientY: 20 });
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("does not count a right-click toward the double-click minimize", () => {
    const onToggleMinimize = vi.fn();
    const { container } = render(
      <DashboardCardWrapper title="Tasks" onToggleMinimize={onToggleMinimize}>
        <div>body</div>
      </DashboardCardWrapper>,
    );
    fireEvent.mouseDown(header(container), { button: 2 });
    fireEvent.mouseDown(header(container), { button: 0 });
    expect(onToggleMinimize).not.toHaveBeenCalled();

    fireEvent.mouseDown(header(container), { button: 0 });
    expect(onToggleMinimize).toHaveBeenCalledTimes(1);
  });
});
