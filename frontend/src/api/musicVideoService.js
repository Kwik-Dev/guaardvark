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

/** Serve URL for a rendered output Document (same pattern as the Video Editor). */
export const documentDownloadUrl = (docId) =>
  `${API_BASE}/files/document/${docId}/download`;
