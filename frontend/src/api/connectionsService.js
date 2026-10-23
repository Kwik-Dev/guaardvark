import { BASE_URL, handleResponse } from "./apiClient";

const base = `${BASE_URL}/connections`;

const request = async (path, options = {}) => {
  const response = await fetch(`${base}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await handleResponse(response);
  if (data?.error) throw new Error(data.error);
  return data;
};

const send = (path, method, body) =>
  request(path, { method, body: JSON.stringify(body ?? {}) });

/** Provider catalog — drives the whole "Add connection" UI. */
export const fetchProviders = async (family) => {
  const query = family ? `?family=${encodeURIComponent(family)}` : "";
  const data = await request(`/providers${query}`);
  return data.providers || [];
};

export const fetchConnections = async (family) => {
  const query = family ? `?family=${encodeURIComponent(family)}` : "";
  const data = await request(query || "");
  return data.connections || [];
};

export const getConnection = (id) => request(`/${id}`);

export const createConnection = (payload) => send("", "POST", payload);

export const updateConnection = (id, payload) => send(`/${id}`, "PUT", payload);

export const deleteConnection = (id) => request(`/${id}`, { method: "DELETE" });

export const testConnection = (id) => send(`/${id}/test`, "POST");

export const startOAuth = (id, redirectUri) =>
  send(`/${id}/oauth/start`, "POST", { redirect_uri: redirectUri });

export const completeOAuth = (id, code, redirectUri) =>
  send(`/${id}/oauth/complete`, "POST", { code, redirect_uri: redirectUri });

/** Credentials detected in the backend process environment (read-only). */
export const fetchEnvironment = async () => {
  const data = await request("/environment");
  return data.environment || [];
};

export const fetchStoreHealth = () => request("/store/health");

export const rotateStoreKey = () => send("/store/rotate-key", "POST");

export const fetchPublishSettings = () => request("/settings");

export const updatePublishSettings = (payload) => send("/settings", "POST", payload);

/** Validate a draft without queueing it — used for live feedback while typing. */
export const preflightPublish = (payload) =>
  send("/publish/preflight", "POST", payload);

export const queuePublish = (payload) => send("/publish", "POST", payload);

export const fetchPublishes = async (params = {}) => {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ""),
  ).toString();
  const data = await request(`/publishes${query ? `?${query}` : ""}`);
  return data.publishes || [];
};

/**
 * Approve a waiting publish. `revision` is the one the person was shown; the
 * backend refuses (409, code "edited_since") when the publish was edited since.
 */
export const approvePublish = (id, revision) =>
  send(`/publishes/${id}/approve`, "POST", revision === undefined ? {} : { revision });

/**
 * Change a publish that waits for approval; it keeps waiting. `payload` holds
 * the changed fields plus `revision`, the one the edit started from.
 * @returns {Promise<{publish: object, changed: string[]}>}
 */
export const editPublish = (id, payload) => send(`/publishes/${id}`, "PATCH", payload);

/** Validate an edit to a waiting publish without saving it. */
export const preflightPublishEdit = (id, payload) =>
  send(`/publishes/${id}/preflight`, "POST", payload);

/** Delete on platform: remove a posted, or platform-scheduled, post. */
export const deletePublishRemote = (id) => send(`/publishes/${id}/delete-remote`, "POST");

/** Per connection: posts so far, last post, scheduled count, rate-limit usage. */
export const fetchConnectionUsage = async (family = "social") => {
  const data = await request(`/usage?family=${encodeURIComponent(family)}`);
  return data.usage || {};
};

export const rejectPublish = (id, reason) =>
  send(`/publishes/${id}/reject`, "POST", { reason });

export const cancelPublish = (id) => send(`/publishes/${id}/cancel`, "POST");
