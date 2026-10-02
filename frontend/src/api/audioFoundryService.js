import axios from "axios";

/**
 * Audio Foundry calls shared by the Audio Studio and the Cast page.
 *
 * A clip is addressed by its file name (`me.wav`), which stays unambiguous
 * when two clips share a name without the extension (`me.wav`, `me.mp3`);
 * the backend also accepts the clip id. Clip mutations send JSON
 * `{confirmed: true}`, after the person confirmed in a dialog.
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

const clipPath = (clip) =>
  `${API_BASE}/audio-foundry/voice-clips/${encodeURIComponent(clip?.filename || clip?.id || "")}`;

/** URL an <audio> element plays the clip from. */
export const voiceClipAudioUrl = (clip) => `${clipPath(clip)}/download`;

/**
 * The voice catalog: `{kokoro: {default, groups: [{label, voices: [{id, label, installed}]}]}}`.
 * Answered from the checkout (with `plugin_running: false`) while Audio
 * Foundry is stopped, so it lists the voices either way.
 */
export const getVoiceCatalog = async () => {
  const response = await axios.get(`${API_BASE}/audio-foundry/voices`);
  return response.data;
};

/** Record consent for a clip already imported. */
export const confirmVoiceClipConsent = async (clip) => {
  const response = await axios.post(`${clipPath(clip)}/consent`, { confirmed: true });
  return response.data;
};

/** Remove the clip's consent record; the clip stays and cannot be cloned. */
export const withdrawVoiceClipConsent = async (clip) => {
  const response = await axios.delete(`${clipPath(clip)}/consent`, { data: { confirmed: true } });
  return response.data;
};

/**
 * Delete the clip and its consent record. Needs the Guaardvark machine or
 * this install's API key; a refusal carries `authRefused` (apiAuth.js).
 */
export const deleteVoiceClip = async (clip) => {
  const response = await axios.delete(clipPath(clip), { data: { confirmed: true } });
  return response.data;
};
