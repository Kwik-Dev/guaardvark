import { BASE_URL, handleResponse } from "./apiClient";

const ROOT = `${BASE_URL}/settings/inbound_guard`;

const post = async (url, body) =>
  handleResponse(
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }),
  );

export const inboundGuardService = {
  async getState() {
    return handleResponse(await fetch(ROOT));
  },

  async setMode(mode) {
    return post(ROOT, { mode });
  },

  // git: include verdicts the git hooks recorded. Leave it off when polling:
  // the database list alone is cheap.
  async listScans(status = "open", limit = 50, { git = true } = {}) {
    return handleResponse(
      await fetch(`${ROOT}/scans?status=${encodeURIComponent(status)}&limit=${limit}&git=${git ? 1 : 0}`),
    );
  },

  async approveScan(id, { note = "", overrideBlock = false } = {}) {
    return post(`${ROOT}/scans/${id}/approve`, { note, override_block: overrideBlock });
  },

  async rejectScan(id, { note = "" } = {}) {
    return post(`${ROOT}/scans/${id}/reject`, { note });
  },

  async getScan(id) {
    return handleResponse(await fetch(`${ROOT}/scans/${id}`));
  },

  async decide(id, decision, { note = "", overrideBlock = false } = {}) {
    return post(`${ROOT}/scans/${id}/decide`, { decision, note, override_block: overrideBlock });
  },

  async sweep() {
    return post(`${ROOT}/sweep`, {});
  },

  async approveGit(digest, note = "") {
    return post(`${ROOT}/git/${encodeURIComponent(digest)}/approve`, { note });
  },
};
