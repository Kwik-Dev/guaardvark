import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import VlmReviewPill from "./VlmReviewPill";

describe("VlmReviewPill", () => {
  it("renders nothing when the clip was not sent for review", () => {
    const { container } = render(<VlmReviewPill review={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the score of a reviewed clip", () => {
    render(<VlmReviewPill review={{ status: "reviewed", available: true, review: { quality_score: 7 } }} />);
    expect(screen.getByText("QA 7/10")).toBeInTheDocument();
  });

  it("shows a missing model as not reviewed, not as a pass", () => {
    render(<VlmReviewPill review={{
      status: "not_reviewed", available: false, reason: "model_not_installed",
      message: "the review model minicpm-v4.5:latest is not installed",
    }} />);
    expect(screen.getByText("Not reviewed")).toBeInTheDocument();
    expect(screen.queryByText(/QA/)).not.toBeInTheDocument();
  });

  it("reads an older record that has no status", () => {
    render(<VlmReviewPill review={{ available: false, reason: "unparseable_review" }} />);
    expect(screen.getByText("Not reviewed")).toBeInTheDocument();
  });
});
