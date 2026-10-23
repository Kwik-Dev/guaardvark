import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import MusicVideoProgress from "./MusicVideoProgress";

const progress = {
  overall_pct: 47.5,
  eta_seconds: 750,
  eta_basis: "measured",
  elapsed_seconds: 600,
  current_clip: { index: 1, position: 2, substage_label: "Animating (I2V)", step: 14, steps: 30 },
  stages: [
    { key: "analyzing", label: "Analyzing song", state: "done", duration_s: 40 },
    { key: "awaiting_approval", label: "Your approval", state: "done" },
    { key: "storyboards", label: "Storyboards", state: "skipped" },
    { key: "generating", label: "Generating clips", state: "active", pct: 42, detail: { clip_count: 3 } },
    { key: "assembling", label: "Assembling", state: "pending" },
  ],
};
const clips = [
  { index: 0, status: "done" },
  { index: 1, status: "pending" },
  { index: 2, status: "pending" },
];

describe("MusicVideoProgress", () => {
  it("renders nothing without progress", () => {
    const { container } = render(<MusicVideoProgress progress={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows stages, ETA, the clip in flight and one cell per cut", () => {
    const onCutClick = vi.fn();
    render(<MusicVideoProgress progress={progress} clips={clips} onCutClick={onCutClick} />);
    expect(screen.getByText("Analyzing song")).toBeInTheDocument();
    expect(screen.queryByText("Storyboards")).not.toBeInTheDocument();
    expect(screen.getByText(/~12m 30s left \(measured\)/)).toBeInTheDocument();
    expect(screen.getByText("Clip 2/3 · Animating (I2V) 14/30")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Cut 1 rendering"));
    expect(onCutClick).toHaveBeenCalledWith(1);
  });

  it("has a compact form for lists", () => {
    render(<MusicVideoProgress progress={progress} compact />);
    expect(screen.getByTestId("mv-progress-compact")).toHaveTextContent("48% · Generating clips");
  });
});
