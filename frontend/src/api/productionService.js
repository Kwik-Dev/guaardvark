import axios from "axios";

/**
 * Production Service for Film Crew page.
 * Handles productions, subjects (casting), and storyboard approvals.
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export const listProductions = async () => {
  const response = await axios.get(`${API_BASE}/production`);
  return response.data;
};

export const getProduction = async (id) => {
  const response = await axios.get(`${API_BASE}/production/${id}`);
  return response.data;
};

export const listProductionSubjects = async (id) => {
  const response = await axios.get(`${API_BASE}/production/${id}/subjects`);
  return response.data;
};

export const createProduction = async (data) => {
  const response = await axios.post(`${API_BASE}/production`, data);
  return response.data;
};

export const castSubject = async (productionId, subjectId, data) => {
  const response = await axios.post(`${API_BASE}/production/${productionId}/cast/${subjectId}`, data);
  return response.data;
};

export const confirmCasting = async (productionId) => {
  const response = await axios.post(`${API_BASE}/production/${productionId}/casting/confirm`);
  return response.data;
};

export const approveStoryboard = async (productionId) => {
  const response = await axios.post(`${API_BASE}/production/${productionId}/storyboard/approve`);
  return response.data;
};

export const regenerateShot = async (productionId, shotId, data) => {
  const response = await axios.post(`${API_BASE}/production/${productionId}/storyboard/shot/${shotId}/regenerate`, data);
  return response.data;
};

export const listCastLibrary = async () => {
  const response = await axios.get(`${API_BASE}/cast-library`);
  return response.data;
};

export const createCastSubject = async (data) => {
  const response = await axios.post(`${API_BASE}/cast-library/subjects`, data);
  return response.data;
};

export const deleteCastSubject = async (id) => {
  await axios.delete(`${API_BASE}/cast-library/subjects/${id}`);
};

// Remove a single reference image (by its index in ref_image_paths). Returns the
// updated subject so the caller can refresh the thumbnail grid.
export const deleteSubjectRef = async (id, index) => {
  const response = await axios.delete(`${API_BASE}/cast-library/subjects/${id}/refs/${index}`);
  return response.data;
};

// ── Cast & LoRA Studio (Casting Director) ────────────────────────────────────
// No single-subject GET on the backend — reuse the list and pick the id.
export const getCastSubject = async (id) => {
  const { subjects = [] } = await listCastLibrary();
  return subjects.find((s) => String(s.id) === String(id)) || null;
};

export const updateCastSubject = async (id, patch) => {
  const response = await axios.patch(`${API_BASE}/cast-library/subjects/${id}`, patch);
  return response.data;
};

// Casting Director: plan the bible + N shot prompts (SYNC — returns {bible, samples}).
export const planCharacter = async (id, body = {}) => {
  const response = await axios.post(`${API_BASE}/cast-library/subjects/${id}/plan`, body);
  return response.data;
};

// Dispatch the FLUX image loop for the whole sheet (ASYNC — returns {task_id}).
export const generateSamples = async (id) => {
  const response = await axios.post(`${API_BASE}/cast-library/subjects/${id}/generate`);
  return response.data;
};

export const listSamples = async (id) => {
  const response = await axios.get(`${API_BASE}/cast-library/subjects/${id}/samples`);
  return response.data;
};

// Regenerate one sample (ASYNC). body: { prompt_override?, seed? }.
export const regenerateSample = async (id, sampleId, body = {}) => {
  const response = await axios.post(
    `${API_BASE}/cast-library/subjects/${id}/samples/${sampleId}/regenerate`,
    body,
  );
  return response.data;
};

export const deleteSample = async (id, sampleId) => {
  await axios.delete(`${API_BASE}/cast-library/subjects/${id}/samples/${sampleId}`);
};

export const approveSamples = async (id, sampleIds, approved = true) => {
  const response = await axios.post(
    `${API_BASE}/cast-library/subjects/${id}/samples/approve`,
    { sample_ids: sampleIds, approved },
  );
  return response.data;
};

// Kick off LoRA training for this subject (ASYNC — returns {task_id}).
export const trainSubject = async (id) => {
  const response = await axios.post(`${API_BASE}/cast-library/subjects/${id}/train`);
  return response.data;
};

const productionService = {
  listProductions,
  getProduction,
  listProductionSubjects,
  createProduction,
  castSubject,
  confirmCasting,
  approveStoryboard,
  regenerateShot,
  listCastLibrary,
  createCastSubject,
  deleteCastSubject,
  getCastSubject,
  updateCastSubject,
  planCharacter,
  generateSamples,
  listSamples,
  regenerateSample,
  approveSamples,
  deleteSample,
  trainSubject,
};

export default productionService;
