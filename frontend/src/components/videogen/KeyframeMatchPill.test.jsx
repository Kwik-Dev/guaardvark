import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import KeyframeMatchPill from "./KeyframeMatchPill";

describe("KeyframeMatchPill", () => {
  it("renders nothing for a clip checked by colour", () => {
    const { container } = render(<KeyframeMatchPill quality={{ colour_match: { score: 0.9 } }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the encoder could not score the clip", () => {
    const { container } = render(<KeyframeMatchPill quality={{ keyframe_match: {
      score: null, reason: "the keyframe match could not run",
    } }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the keyframe match as a percentage", () => {
    render(<KeyframeMatchPill quality={{ keyframe_match: {
      label: "keyframe match", method: "embed", score: 0.93, frame_index: 37, frames: 75, threshold: 0.5,
    } }} />);
    expect(screen.getByText("Keyframe 93%")).toBeInTheDocument();
  });
});
