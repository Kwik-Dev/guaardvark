/**
 * Words for the voice session's state, shared by the mic buttons, their
 * popover and the Voice page.
 */

const CLICK_HINT = {
  handsfree: "Click to start hands-free listening",
  toggle: "Click and speak; it sends when you pause",
  push: "Hold to talk",
};

/**
 * @param {object} state voice session store state
 * @param {{systemName?: string, mode?: string}} context
 * @returns {{title: string, hint: string}}
 */
export function describeVoicePhase(state, { systemName = "Guaardvark", mode = "handsfree" } = {}) {
  const s = state || {};
  switch (s.phase) {
    case "requesting-permission":
      return { title: "Waiting for microphone permission", hint: "Allow the microphone in the browser prompt." };
    case "listening":
      return s.listenMode === "passive"
        ? { title: `Waiting for “Hey ${systemName}”`, hint: "Say the wake phrase, then your request." }
        : { title: "Listening", hint: "Speak; each pause sends a message. Click to stop." };
    case "capturing":
      return s.push
        ? { title: "Recording", hint: "Release to send." }
        : { title: "Hearing you", hint: "Pause to send." };
    case "transcribing":
      return { title: "Transcribing", hint: "" };
    case "sending":
      return { title: "Sending to chat", hint: "" };
    case "responding":
      return { title: "Waiting for the reply", hint: s.session ? "Still listening." : "" };
    case "speaking":
      return { title: "Speaking the reply", hint: "The mic is paused while it speaks." };
    case "denied":
      return { title: "Microphone blocked", hint: s.error || "" };
    case "error":
      return { title: "Voice is not working", hint: s.error || "" };
    default:
      return { title: "Mic off", hint: CLICK_HINT[mode] || CLICK_HINT.handsfree };
  }
}

export const OUTCOME_LABELS = Object.freeze({
  sent: "sent",
  wake: "wake phrase",
  ignored: "no wake phrase, not sent",
  unsent: "no chat open, not sent",
  failed: "send failed",
});
