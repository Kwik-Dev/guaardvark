import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const listChatSessions = vi.fn();
const deleteChatSession = vi.fn();
vi.mock("../../../api/chatService", () => ({
  listChatSessions: (...args) => listChatSessions(...args),
  deleteChatSession: (...args) => deleteChatSession(...args),
}));

import ChatSessionDrawer from "../ChatSessionDrawer";

const SESSION = { session_id: "s1", preview: "Plan the launch", message_count: 4, created_at: new Date().toISOString() };

const renderDrawer = () =>
  render(<ChatSessionDrawer open onClose={() => {}} currentSessionId="other" onSelectSession={() => {}} onNewChat={() => {}} />);

beforeEach(() => {
  listChatSessions.mockReset();
  deleteChatSession.mockReset();
});

describe("chat session drawer failures", () => {
  it("shows a failed load as an error with Retry, not as 'No chats yet'", async () => {
    listChatSessions.mockRejectedValueOnce(new Error("HTTP error 500 - Internal Server Error"));
    listChatSessions.mockResolvedValueOnce({ sessions: [SESSION] });
    renderDrawer();

    expect(await screen.findByText("HTTP error 500 - Internal Server Error")).toBeInTheDocument();
    expect(screen.queryByText("No chats yet")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Plan the launch")).toBeInTheDocument();
    expect(screen.queryByText("HTTP error 500 - Internal Server Error")).not.toBeInTheDocument();
  });

  it("keeps the dialog open and says why when the delete fails", async () => {
    listChatSessions.mockResolvedValue({ sessions: [SESSION] });
    deleteChatSession.mockRejectedValue(new Error("Session is locked"));
    renderDrawer();
    await screen.findByText("Plan the launch");

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete chat" }));

    expect(await within(dialog).findByText("The chat was not deleted: Session is locked.")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getAllByText("Plan the launch").length).toBeGreaterThan(0);
  });
});
