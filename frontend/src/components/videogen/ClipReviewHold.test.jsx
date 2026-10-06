import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import ClipReviewHold, { ReviewStatePill } from "./ClipReviewHold";

const held = {
  state: "needs_review",
  codes: ["washed_out"],
  reasons: ["washed out (low contrast, lifted blacks): luma spread 13 of 255"],
};

describe("ClipReviewHold", () => {
  it("renders nothing for a clip no check held", () => {
    const { container } = render(<ClipReviewHold review={null} onApprove={() => {}} onRerender={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says why the clip is held and offers approve and re-render", () => {
    const onApprove = vi.fn();
    const onRerender = vi.fn();
    render(<ClipReviewHold review={held} onApprove={onApprove} onRerender={onRerender} />);
    expect(screen.getByText(/Held for review: washed out/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Approve"));
    fireEvent.click(screen.getByText("Re-render"));
    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onRerender).toHaveBeenCalledTimes(1);
  });

  it("shows no buttons once a person has approved it", () => {
    render(<ClipReviewHold review={{ ...held, state: "approved" }} onApprove={() => {}} onRerender={() => {}} />);
    expect(screen.queryByText("Approve")).not.toBeInTheDocument();
  });
});

describe("ReviewStatePill", () => {
  it("names each review state", () => {
    const { rerender } = render(<ReviewStatePill review={held} />);
    expect(screen.getByText("Needs review")).toBeInTheDocument();
    rerender(<ReviewStatePill review={{ ...held, state: "approved" }} />);
    expect(screen.getByText("Approved")).toBeInTheDocument();
    rerender(<ReviewStatePill review={{ state: "rerendered", rerender_batch_id: "VideoBatch_x" }} />);
    expect(screen.getByText("Re-rendered")).toBeInTheDocument();
  });
});
