import { create } from "zustand";

/**
 * Live state of the voice session, written only by VoiceSessionEngine.
 *
 * Not persisted: it describes a microphone that is or is not open right now.
 * `phase` is derived from the other fields (derivePhase) so it can never
 * disagree with them; components select the fields they show.
 */
export const INITIAL_VOICE_SESSION = Object.freeze({
  phase: "idle",
  // "handsfree" | "utterance" | null: what the current click started.
  session: null,
  // "passive" waits for the wake phrase; "active" sends what it hears.
  listenMode: "active",
  push: false,
  micOpen: false,
  requesting: false,
  capturing: false,
  pendingStt: 0,
  sending: false,
  awaitingReply: false,
  speaking: false,
  permission: "unknown",
  level: 0,
  lastTranscript: null,
  transcripts: [],
  error: null,
  errorCode: null,
  notice: null,
});

/**
 * The one state the mic buttons show, highest priority first:
 * error/denied, requesting-permission, speaking, capturing, transcribing,
 * sending, responding, listening, idle.
 */
export function derivePhase(s) {
  if (s.error) return s.errorCode === "denied" ? "denied" : "error";
  if (s.requesting) return "requesting-permission";
  if (s.speaking) return "speaking";
  if (s.capturing || s.push) return "capturing";
  if (s.pendingStt > 0) return "transcribing";
  if (s.sending) return "sending";
  if (s.awaitingReply) return "responding";
  if (s.session && s.micOpen) return "listening";
  return "idle";
}

export const useVoiceSessionStore = create(() => ({ ...INITIAL_VOICE_SESSION }));
