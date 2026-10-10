import React from "react";
import { render, screen, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

// The session list serves each session's stored mode; the badge reads that,
// never the words in the preview.
vi.mock("../../../api/chatService", () => ({
  listChatSessions: vi.fn(async () => ({
    sessions: [
      { session_id: "s-agent", mode: "agent", preview: "Open the settings page", message_count: 2, created_at: new Date().toISOString() },
      { session_id: "s-chat", mode: "chat", preview: "Tell me about the agent pattern", message_count: 2, created_at: new Date().toISOString() },
      { session_id: "s-old", preview: "self-test monitor agent", message_count: 1, created_at: new Date().toISOString() },
    ],
  })),
  deleteChatSession: vi.fn(async () => ({})),
}));

import ChatSessionDrawer from "../ChatSessionDrawer";

const rowOf = (text) => screen.getByText(text).closest("li");

describe("chat session Agent badge", () => {
  it("follows the session's stored mode, not the preview text", async () => {
    render(
      <ChatSessionDrawer open onClose={() => {}} currentSessionId="other" onSelectSession={() => {}} onNewChat={() => {}} />,
    );
    await screen.findByText("Open the settings page");

    expect(within(rowOf("Open the settings page")).getByText("Agent")).toBeInTheDocument();
    expect(within(rowOf("Tell me about the agent pattern")).queryByText("Agent")).not.toBeInTheDocument();
    // No mode in the row (an older list) means a chat session.
    expect(within(rowOf("self-test monitor agent")).queryByText("Agent")).not.toBeInTheDocument();
  });
});
