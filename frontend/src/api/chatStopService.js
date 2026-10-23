/**
 * Stopping a chat turn: the one place the UI asks the backend to stop.
 *
 * Stop sends two requests: abort the session's chat turn, and kill any agent
 * task it started (the socket abort alone leaves the see-think-act loop
 * running). Both go through handleResponse, so an offline backend or a
 * refused request comes back as a specific error instead of nothing.
 */
import { BASE_URL, handleResponse } from "./apiClient";

const post = (url) =>
  fetch(url, { method: "POST" }).then((res) => handleResponse(res, { quiet: true }));

/**
 * Ask the backend to stop the session's turn and any agent task.
 * Never throws. Resolves to `{ ok: true }`, or `{ ok: false, error }` with
 * a message naming what could not be stopped.
 */
export async function stopChatTurn(sessionId) {
  const results = await Promise.allSettled([
    sessionId
      ? post(`${BASE_URL}/chat/unified/${encodeURIComponent(sessionId)}/abort`)
      : Promise.resolve(null),
    post(`${BASE_URL}/agent-control/kill`),
  ]);
  const [abort, kill] = results;
  const failures = [];
  if (abort.status === "rejected") failures.push(["the chat turn", abort.reason]);
  if (kill.status === "rejected") failures.push(["the agent task", kill.reason]);
  if (!failures.length) return { ok: true };
  const offline = failures.some(([, err]) => err?.backendOffline);
  const what = failures.map(([name]) => name).join(" and ");
  const why = offline
    ? "the backend is not reachable"
    : failures.map(([, err]) => err?.message || String(err)).join("; ");
  return {
    ok: false,
    error: `Stop did not reach ${what}: ${why}. It may still be running.`,
  };
}
