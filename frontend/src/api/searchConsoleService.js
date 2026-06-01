// frontend/src/api/searchConsoleService.js
// Service for Google Search Console / Indexing API URL submission, per website.
import { BASE_URL, handleResponse } from "./apiClient";

const base = (websiteId) => `${BASE_URL}/search-console/${websiteId}`;

export const getIndexingStatus = async (websiteId) => {
  if (!websiteId) return { error: "Website ID is required." };
  try {
    const response = await fetch(`${base(websiteId)}/status`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `searchConsoleService: Error getting status for ${websiteId}:`,
      err.message,
    );
    throw err;
  }
};

export const syncSitemap = async (websiteId) => {
  if (!websiteId) return { error: "Website ID is required." };
  try {
    const response = await fetch(`${base(websiteId)}/sync`, { method: "POST" });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `searchConsoleService: Error syncing sitemap for ${websiteId}:`,
      err.message,
    );
    throw err;
  }
};

export const submitToIndex = async (websiteId, options = {}) => {
  if (!websiteId) return { error: "Website ID is required." };
  try {
    const response = await fetch(`${base(websiteId)}/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `searchConsoleService: Error submitting ${websiteId} to index:`,
      err.message,
    );
    throw err;
  }
};

export const updateIndexingConfig = async (websiteId, config = {}) => {
  if (!websiteId) return { error: "Website ID is required." };
  try {
    const response = await fetch(`${base(websiteId)}/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `searchConsoleService: Error updating config for ${websiteId}:`,
      err.message,
    );
    throw err;
  }
};

export const getSubmissions = async (websiteId, params = {}) => {
  if (!websiteId) return { error: "Website ID is required." };
  try {
    const qs = new URLSearchParams(params).toString();
    const response = await fetch(`${base(websiteId)}/submissions${qs ? `?${qs}` : ""}`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return Array.isArray(data) ? data : [];
  } catch (err) {
    console.error(
      `searchConsoleService: Error getting submissions for ${websiteId}:`,
      err.message,
    );
    throw err;
  }
};
