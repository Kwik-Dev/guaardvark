/**
 * Shared Outreach API client — used by OutreachPage and /outreach slash.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

async function parseError(res) {
  const body = await res.json().catch(() => ({}));
  return body.error || body.message || `HTTP ${res.status}`;
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}/social-outreach${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    throw new Error(await parseError(res));
  }
  if (res.status === 204) return null;
  return res.json();
}

export function getOutreachBaseUrl() {
  return BASE_URL;
}

export function fetchStatus() {
  return request("/status");
}

export function fetchQueue() {
  return request("/queue");
}

export function fetchApproved() {
  return request("/approved");
}

export function fetchAudit(limit = 100) {
  return request(`/audit?limit=${limit}`);
}

export function fetchSnippets() {
  return request("/snippets");
}

export function enableOutreach() {
  return request("/enable", { method: "POST" });
}

export function killOutreach() {
  return request("/kill", { method: "POST" });
}

export function setSupervised(on) {
  return request("/supervised", {
    method: "POST",
    body: JSON.stringify({ on: !!on }),
  });
}

export function approveDraft(id, draftText) {
  const body = {};
  if (draftText != null) body.draft_text = draftText;
  return request(`/approve/${id}`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function rejectDraft(id) {
  return request(`/reject/${id}`, { method: "POST", body: "{}" });
}

export function patchDraft(id, payload) {
  return request(`/drafts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function createDraft(payload) {
  return request("/drafts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function draftComment(payload) {
  return request("/draft-comment", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function scoutUrl(url) {
  return request("/scout-url", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export function fetchMeta(url) {
  return request("/fetch-meta", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export function runPass(payload) {
  return request("/run-pass", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function executeIntent(payload) {
  return request("/intent", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export default {
  getOutreachBaseUrl,
  fetchStatus,
  fetchQueue,
  fetchApproved,
  fetchAudit,
  fetchSnippets,
  enableOutreach,
  killOutreach,
  setSupervised,
  approveDraft,
  rejectDraft,
  patchDraft,
  createDraft,
  draftComment,
  scoutUrl,
  fetchMeta,
  runPass,
  executeIntent,
};
