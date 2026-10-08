import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
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

const assistant = (overrides) => ({
  role: "assistant",
  content: "Your notes are kept in the Projects folder.",
  timestamp: "2026-10-06T12:00:00Z",
  ...overrides,
});

describe("MessageItem facts-check note", () => {
  it("shows the note when the agent's answer was not supported by its tool results", () => {
    renderMsg(assistant({ verified: false }));
    expect(screen.getByTestId("unverified-note")).toHaveTextContent(
      "not checked against the tool results",
    );
  });

  it("shows nothing for a supported answer", () => {
    renderMsg(assistant({ verified: true }));
    expect(screen.queryByTestId("unverified-note")).not.toBeInTheDocument();
  });

  it("shows nothing when there was nothing to check", () => {
    renderMsg(assistant({ verified: null }));
    expect(screen.queryByTestId("unverified-note")).not.toBeInTheDocument();
    renderMsg(assistant({}));
    expect(screen.queryByTestId("unverified-note")).not.toBeInTheDocument();
  });

  it("never labels the person's own message", () => {
    renderMsg({ role: "user", content: "where are my notes?", verified: false });
    expect(screen.queryByTestId("unverified-note")).not.toBeInTheDocument();
  });
});
