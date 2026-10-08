import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import MessageItem from "../MessageItem";

vi.mock("../../../stores/useAppStore", () => ({
  useAppStore: (sel) => {
    const state = { systemLogo: null, activeLessonId: null };
    return typeof sel === "function" ? sel(state) : state;
  },
}));

vi.mock("../../common/NarrateButton", () => ({
  default: () => null,
}));

const renderMsg = (message) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <MessageItem message={message} sessionId="session_1" />
    </ThemeProvider>,
  );

describe("MessageItem right-click", () => {
  afterEach(() => window.getSelection()?.removeAllRanges());

  it("offers Copy and the thumbs on an assistant reply", () => {
    renderMsg({ role: "assistant", content: "The notes are in the Projects folder.", timestamp: "2026-10-06T12:00:00Z" });
    fireEvent.contextMenu(screen.getByText("The notes are in the Projects folder."), { clientX: 20, clientY: 20 });
    expect(screen.getAllByRole("menuitem").map((el) => el.textContent)).toEqual([
      "Copy",
      "Good response",
      "Bad response",
    ]);
  });

  it("offers only Copy on a user message", () => {
    renderMsg({ role: "user", content: "where are my notes?", timestamp: "2026-10-06T12:00:00Z" });
    fireEvent.contextMenu(screen.getByText("where are my notes?"), { clientX: 20, clientY: 20 });
    expect(screen.getAllByRole("menuitem").map((el) => el.textContent)).toEqual(["Copy"]);
  });

  it("leaves the browser menu when text in the message is selected", () => {
    renderMsg({ role: "user", content: "where are my notes?", timestamp: "2026-10-06T12:00:00Z" });
    const text = screen.getByText("where are my notes?");
    const range = document.createRange();
    range.selectNodeContents(text);
    window.getSelection().addRange(range);
    expect(fireEvent.contextMenu(text, { clientX: 20, clientY: 20 })).toBe(true);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
