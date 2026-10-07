import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import useFileDropZone from "./useFileDropZone";

function Zone({ onFiles, enabled }) {
  const drop = useFileDropZone({ onFiles, enabled });
  return (
    <div data-testid="zone" {...drop.dropProps}>
      <div data-testid="list">messages</div>
      <textarea aria-label="composer" />
      {drop.isDragActive && <span>highlight</span>}
    </div>
  );
}

const fileDrag = (files = []) => ({ dataTransfer: { files, types: ["Files"] } });
const pdf = () => new File(["%PDF-1.4"], "report.pdf", { type: "application/pdf" });

describe("useFileDropZone", () => {
  it("takes a file dropped on a child and cancels the browser's default", () => {
    const onFiles = vi.fn();
    render(<Zone onFiles={onFiles} />);
    const file = pdf();
    expect(fireEvent.dragOver(screen.getByTestId("list"), fileDrag())).toBe(false);
    expect(fireEvent.drop(screen.getByTestId("list"), fileDrag([file]))).toBe(false);
    expect(onFiles).toHaveBeenCalledWith([file]);
  });

  it("does not let the drop reach an outer handler", () => {
    const outer = vi.fn();
    render(
      <div onDrop={outer}>
        <Zone onFiles={() => {}} />
      </div>,
    );
    fireEvent.drop(screen.getByTestId("list"), fileDrag([pdf()]));
    expect(outer).not.toHaveBeenCalled();
  });

  it("highlights while files are over it, across its children", () => {
    render(<Zone onFiles={() => {}} />);
    fireEvent.dragEnter(screen.getByTestId("zone"), fileDrag());
    fireEvent.dragEnter(screen.getByTestId("list"), fileDrag());
    fireEvent.dragLeave(screen.getByTestId("zone"), fileDrag());
    expect(screen.getByText("highlight")).toBeInTheDocument();
    fireEvent.dragLeave(screen.getByTestId("list"), fileDrag());
    expect(screen.queryByText("highlight")).not.toBeInTheDocument();
  });

  it("leaves a text drag to the browser", () => {
    const onFiles = vi.fn();
    render(<Zone onFiles={onFiles} />);
    const textDrag = { dataTransfer: { files: [], types: ["text/plain"] } };
    expect(fireEvent.dragOver(screen.getByLabelText("composer"), textDrag)).toBe(true);
    expect(fireEvent.drop(screen.getByLabelText("composer"), textDrag)).toBe(true);
    expect(onFiles).not.toHaveBeenCalled();
  });

  it("swallows but ignores drops while disabled", () => {
    const onFiles = vi.fn();
    render(<Zone onFiles={onFiles} enabled={false} />);
    expect(fireEvent.drop(screen.getByTestId("list"), fileDrag([pdf()]))).toBe(false);
    expect(onFiles).not.toHaveBeenCalled();
  });
});
