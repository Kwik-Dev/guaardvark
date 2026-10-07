import React from "react";
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import TipsSetting from "../TipsSetting";
import { useAppStore } from "../../../stores/useAppStore";

const renderSetting = () =>
  render(
    <ThemeProvider theme={createTheme()}>
      <TipsSetting />
    </ThemeProvider>,
  );

describe("TipsSetting", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useAppStore.setState({ tipsEnabled: true });
  });

  it("is on by default and switches tips off and on", () => {
    renderSetting();
    const chip = screen.getByRole("switch", { name: "Did you know tips" });
    expect(chip).toHaveAttribute("aria-checked", "true");

    fireEvent.click(chip);
    expect(useAppStore.getState().tipsEnabled).toBe(false);
    expect(chip).toHaveAttribute("aria-checked", "false");
    expect(JSON.parse(window.localStorage.getItem("guaardvark-app-storage")).state.tipsEnabled).toBe(false);

    fireEvent.click(chip);
    expect(useAppStore.getState().tipsEnabled).toBe(true);
  });

  it("shows what the card's Don't show tips button did", () => {
    useAppStore.setState({ tipsEnabled: false });
    renderSetting();
    expect(screen.getByRole("switch", { name: "Did you know tips" })).toHaveAttribute("aria-checked", "false");
  });

  it("explains itself on hover over the label", async () => {
    renderSetting();
    fireEvent.mouseOver(screen.getByText("Tips"));
    await waitFor(() => expect(screen.getByRole("tooltip")).toHaveTextContent("at most one per visit"));
  });
});
