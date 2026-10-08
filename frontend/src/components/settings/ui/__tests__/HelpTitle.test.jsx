import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SettingsPanel, Cluster, DashboardTile } from "..";

describe("help on titles", () => {
  it("shows a panel's help on hover over its title, not as text under it", async () => {
    render(
      <SettingsPanel title="Models" help="Which model answers chat.">
        body
      </SettingsPanel>,
    );
    expect(screen.queryByText("Which model answers chat.")).not.toBeInTheDocument();
    fireEvent.mouseOver(screen.getByText("Models"));
    await waitFor(() => expect(screen.getByRole("tooltip")).toHaveTextContent("Which model answers chat."));
  });

  it("puts help on a cluster label and keeps its note visible", async () => {
    render(
      <Cluster label="Embedding" help="Turns text into vectors." note="set by .env">
        x
      </Cluster>,
    );
    expect(screen.getByText("set by .env")).toBeInTheDocument();
    fireEvent.mouseOver(screen.getByText("Embedding"));
    await waitFor(() => expect(screen.getByRole("tooltip")).toHaveTextContent("Turns text into vectors."));
  });

  it("puts help on a tile label", async () => {
    render(<DashboardTile label="GPU" help="Video memory in use." value="3 GB" />);
    fireEvent.mouseOver(screen.getByText("GPU"));
    await waitFor(() => expect(screen.getByRole("tooltip")).toHaveTextContent("Video memory in use."));
  });

  it("renders a plain title when there is no help", () => {
    render(<SettingsPanel title="About">body</SettingsPanel>);
    expect(screen.getByText("About").closest("[tabindex]")).toBeNull();
  });
});
