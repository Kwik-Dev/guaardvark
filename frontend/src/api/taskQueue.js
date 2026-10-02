// frontend/src/api/taskQueue.js
// What the UI says when work could not be handed to the background worker.
//
// Routes that start background work send it through Redis to the Celery
// worker. When Redis is not reachable they answer 503 with the code
// task_queue_unreachable (backend/celery_dispatch.py); the server's text names
// an internal task, so the web UI shows QUEUE_UNREACHABLE_MESSAGE instead and
// keeps the server's words in server_error. installQueueMessages() does this
// for axios; handleResponse (apiClient.js) does it for fetch.
//
// Routes that move a Film Crew or music video project forward still succeed
// when its next step could not be queued: they answer {dispatched: false,
// warning}, and the project resumes when Guaardvark restarts. dispatchWarning()
// picks that out for a page to show.

export const TASK_QUEUE_UNREACHABLE = "task_queue_unreachable";

export const QUEUE_UNREACHABLE_MESSAGE =
  "Not started: Guaardvark's background queue (Redis) is not reachable. " +
  "Run ./start.sh on the Guaardvark machine to start Redis and the background worker, then try again.";

const INSTALLED = Symbol.for("guaardvark.queueMessages");

/** True for an error body from a route whose task was not started. */
export function isQueueUnreachable(data) {
  if (!data || typeof data !== "object") return false;
  return data.code === TASK_QUEUE_UNREACHABLE || data.error?.code === TASK_QUEUE_UNREACHABLE;
}

/**
 * The warning a route returned when it saved the change but could not queue
 * the next step, or null when the step was queued (or none was due).
 */
export function dispatchWarning(result) {
  if (!result || result.dispatched !== false) return null;
  return typeof result.warning === "string" && result.warning
    ? result.warning
    : "The next step was not started. It starts when Guaardvark is restarted (./start.sh).";
}

/** Word axios errors for a task that was not started; installed once. */
export function installQueueMessages({ axios } = {}) {
  if (!axios || axios[INSTALLED]) return;
  axios.interceptors.response.use(
    (response) => response,
    (error) => {
      const response = error?.response;
      if (response && response.status === 503 && isQueueUnreachable(response.data)) {
        response.data = {
          ...response.data,
          error: QUEUE_UNREACHABLE_MESSAGE,
          message: QUEUE_UNREACHABLE_MESSAGE,
          server_error: response.data.error,
        };
        error.message = QUEUE_UNREACHABLE_MESSAGE;
        error.queueUnreachable = true;
      }
      return Promise.reject(error);
    },
  );
  axios[INSTALLED] = true;
}
