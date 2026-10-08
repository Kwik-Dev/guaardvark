import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";

const getSpeechModel = vi.fn();
const setSpeechModel = vi.fn();
vi.mock("../../../api/voiceService", () => ({
  default: {
    getSpeechModel: (...args) => getSpeechModel(...args),
    setSpeechModel: (...args) => setSpeechModel(...args),
  },
}));

import SpeechModelSelect from "../SpeechModelSelect";

const TINY = { id: "tiny.en", name: "Tiny English (Fastest)" };
const TURBO = { id: "large-v3-turbo", name: "Large v3 Turbo (Most accurate)" };

const payload = (over = {}) => ({
  success: true,
  data: {
    model: "tiny.en",
    model_name: TINY.name,
    in_use: "tiny.en",
    default_model: "tiny.en",
    installed: [TINY, TURBO],
    ...over,
  },
});

const renderSelect = (props = {}) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <SpeechModelSelect {...props} />
    </ThemeProvider>,
  );

describe("SpeechModelSelect", () => {
  beforeEach(() => {
    getSpeechModel.mockReset();
    setSpeechModel.mockReset();
  });

  it("lists only installed models and shows the one in use", async () => {
    getSpeechModel.mockResolvedValue(payload());
    renderSelect();
    const select = await screen.findByRole("combobox");
    expect(select).toHaveTextContent(TINY.name);
    fireEvent.mouseDown(select);
    const options = within(await screen.findByRole("listbox")).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([TINY.name, TURBO.name]);
  });

  it("saves a new choice and says so", async () => {
    getSpeechModel.mockResolvedValue(payload());
    setSpeechModel.mockResolvedValue(payload({ model: TURBO.id, model_name: TURBO.name, in_use: TURBO.id }));
    const onChange = vi.fn();
    const showMessage = vi.fn();
    renderSelect({ onChange, showMessage });

    fireEvent.mouseDown(await screen.findByRole("combobox"));
    fireEvent.click(await screen.findByRole("option", { name: TURBO.name }));

    await waitFor(() => expect(screen.getByRole("combobox")).toHaveTextContent(TURBO.name));
    expect(setSpeechModel).toHaveBeenCalledWith(TURBO.id);
    expect(onChange).toHaveBeenCalledWith(TURBO.id);
    expect(showMessage).toHaveBeenCalledWith(`Speech recognition now uses ${TURBO.name}`, "success");
  });

  it("reports a refused choice and keeps the one in use", async () => {
    getSpeechModel.mockResolvedValue(payload());
    setSpeechModel.mockRejectedValue(new Error("Large v3 Turbo (Most accurate) is not installed."));
    renderSelect();

    fireEvent.mouseDown(await screen.findByRole("combobox"));
    fireEvent.click(await screen.findByRole("option", { name: TURBO.name }));

    expect(await screen.findByText(/Could not change the speech model: .*is not installed/)).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveTextContent(TINY.name);
  });

  it("says when the chosen model is missing and which one is used instead", async () => {
    getSpeechModel.mockResolvedValue(payload({ model: "small", model_name: "Small (Accurate)", installed: [TINY] }));
    renderSelect();
    expect(
      await screen.findByText("Small (Accurate) is not installed, so Tiny English (Fastest) is used until it is."),
    ).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveTextContent(TINY.name);
  });

  it("says when nothing is installed and opens Voice models", async () => {
    getSpeechModel.mockResolvedValue(payload({ installed: [] }));
    const onManageModels = vi.fn();
    renderSelect({ onManageModels });
    expect(await screen.findByText("No speech model is installed yet.")).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Voice models" }));
    expect(onManageModels).toHaveBeenCalledTimes(1);
  });

  it("reads the list again when its reload key changes", async () => {
    getSpeechModel.mockResolvedValue(payload());
    const { rerender } = renderSelect({ reloadKey: 1 });
    await screen.findByRole("combobox");
    rerender(
      <ThemeProvider theme={createTheme()}>
        <SpeechModelSelect reloadKey={2} />
      </ThemeProvider>,
    );
    await waitFor(() => expect(getSpeechModel).toHaveBeenCalledTimes(2));
  });

  it("explains itself on hover over the label", async () => {
    getSpeechModel.mockResolvedValue(payload());
    renderSelect();
    fireEvent.mouseOver(screen.getByText("Speech recognition model"));
    await waitFor(() => expect(screen.getByRole("tooltip")).toHaveTextContent("Only installed models are listed"));
  });
});
