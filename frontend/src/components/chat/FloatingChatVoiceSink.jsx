import { useEffect } from "react";
import { useVoiceSession } from "../../contexts/VoiceSessionContext";
import { useFloatingChatStore } from "../../stores/useFloatingChatStore";

/**
 * Sends global-mic transcripts to the floating chat.
 *
 * Mounted wherever the floating chat is available, open or not: a turn opens
 * (and expands) the card and joins its queue; FloatingChatCard sends queued
 * turns one at a time. A page that is its own chat (ChatPage) registers with a
 * higher priority and takes over while it is mounted.
 */
const FloatingChatVoiceSink = () => {
  const voice = useVoiceSession();

  useEffect(
    () =>
      voice.registerSink({
        id: "floating-chat",
        priority: 0,
        deliver: (turn) => {
          const store = useFloatingChatStore.getState();
          store.enqueueVoiceTurn(turn);
          if (!store.isOpen) store.setIsOpen(true);
          if (store.collapsed) store.setCollapsed(false);
        },
      }),
    [voice]
  );

  return null;
};

export default FloatingChatVoiceSink;
