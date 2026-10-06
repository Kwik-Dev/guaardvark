import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import ColourMatchPill from "./ColourMatchPill";

describe("ColourMatchPill", () => {
  it("renders nothing when the clip has no colour match", () => {
    const { container } = render(<ColourMatchPill quality={{ flags: [] }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("labels the score a colour match, not identity", () => {
    render(<ColourMatchPill quality={{ colour_match: {
      label: "colour match", score: 0.82, frame_index: 24, frames: 49,
    } }} />);
    expect(screen.getByText("Colour 82%")).toBeInTheDocument();
    expect(screen.queryByText(/^ID /)).not.toBeInTheDocument();
  });

  it("relabels an older batch's histogram score", () => {
    render(<ColourMatchPill quality={{ identity: { method: "hist", score: 0.4 } }} />);
    expect(screen.getByText("Colour 40%")).toBeInTheDocument();
  });

  it("does not show an older file-size ratio", () => {
    const { container } = render(<ColourMatchPill quality={{ identity: { method: "size", score: 0.9 } }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
