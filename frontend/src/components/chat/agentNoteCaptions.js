// Captions under a note sent to the screen agent while it was working.
// Keyed by the message's `agentNote` status (ChatPage, FloatingChatCard).
export const AGENT_NOTE_CAPTIONS = {
  sending: "Note to the agent — sending…",
  sent: "Note to the agent while it worked",
  stopping: "Note to the agent — stopping the run",
  late: "The agent had already finished, so it didn't see this. Send it again if you still want it.",
  failed: "Note not delivered to the agent",
};

export const agentNoteCaption = (status) => AGENT_NOTE_CAPTIONS[status] || AGENT_NOTE_CAPTIONS.sent;
