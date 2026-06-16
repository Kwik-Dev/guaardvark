import axios from "axios";

/**
 * Music Video Service for the Music Video page.
 * Drives the song-driven pipeline: create (→ analyze) / inspect / approve.
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export const listMusicVideos = async () => {
  const response = await axios.get(`${API_BASE}/music-video`);
  return response.data;
};

export const getMusicVideo = async (id) => {
  const response = await axios.get(`${API_BASE}/music-video/${id}`);
  return response.data;
};

export const createMusicVideo = async (data) => {
  const response = await axios.post(`${API_BASE}/music-video`, data);
  return response.data;
};

export const approveMusicVideo = async (id) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/approve`);
  return response.data;
};

/** Clear one past generation from the log. */
export const deleteMusicVideo = async (id) => {
  const response = await axios.delete(`${API_BASE}/music-video/${id}`);
  return response.data;
};

/** Clear finished generations (complete/failed), or all with {all:true}. */
export const clearMusicVideos = async ({ all = false } = {}) => {
  const response = await axios.delete(`${API_BASE}/music-video`, {
    params: all ? { all: true } : {},
  });
  return response.data;
};

/** Serve URL for a rendered output Document (same pattern as the Video Editor). */
export const documentDownloadUrl = (docId) =>
  `${API_BASE}/files/document/${docId}/download`;

/** Update per-cut prompts (and optionally the global style_prompt) before approval.
 *  Body shape: { prompts: { "0": "new prompt for cut 0", ... }, style_prompt?: "..." }
 *  or { clips: [ { index: 0, prompt: "..." }, ... ] }
 */
export const updateMusicVideoPlan = async (id, data) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/plan`, data);
  return response.data;
};

/** Re-run the Director over the existing cut plan (pre-approval only).
 *  Optional: {
 *    feedback: "...",
 *    planning_mode: "narrative" | "visual",
 *    director_model: "gemma4:e4b"   // dedicated small model for the Director agent
 *  }
 */
export const regenerateMusicVideoPlan = async (id, data = {}) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/regenerate-plan`, data);
  return response.data;
};

/** Reset a completed (or failed) music video back to the plan approval stage so it can be re-rendered
 * (with the same or edited treatment/prompts/settings). Useful for re-doing with different seeds, models, or tweaks.
 */
export const replanMusicVideo = async (id) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/replan`);
  return response.data;
};

export const cancelMusicVideo = async (id) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/cancel`);
  return response.data;
};

export const generateMusicVideoStoryboards = async (id) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/generate-storyboards`);
  return response.data;
};

export const regenMusicVideoStoryboard = async (id, index, data = {}) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/regen-storyboard/${index}`, data);
  return response.data;
};
