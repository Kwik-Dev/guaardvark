import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import CutQualityList from "./CutQualityList";

const clean = { index: 0, status: "done", quality: { flagged: false, flags: [] }, review: null };
const held = {
  index: 1,
  status: "done",
  quality: { flagged: true, flags: [{ code: "black_frames", message: "black frames: 3 of 9 sampled frames" }] },
  review: { state: "needs_review", codes: ["black_frames"], reasons: ["black frames: 3 of 9 sampled frames"] },
};

describe("CutQualityList", () => {
  it("renders nothing before any cut has been checked", () => {
    const { container } = render(
      <CutQualityList clips={[{ index: 0, status: "pending" }]} onApprove={() => {}} onRerender={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the flags per cut and offers approve and re-render on a held one", () => {
    const onApprove = vi.fn();
    const onRerender = vi.fn();
    render(<CutQualityList clips={[clean, held]} onApprove={onApprove} onRerender={onRerender} />);
    expect(screen.getByText(/1 held for review/)).toBeInTheDocument();
    expect(screen.getByText("Black frames")).toBeInTheDocument();
    expect(screen.getByText("no problems found")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Approve"));
    fireEvent.click(screen.getByText("Re-render"));
    expect(onApprove).toHaveBeenCalledWith(1);
    expect(onRerender).toHaveBeenCalledWith(1);
  });
});
