// What the Image Gen gallery has to follow between polls of /batch-image/queue and
// image_generation progress events: batches that finished, grew, or appeared from
// somewhere else (chat, MCP, another tab).

export const ACTIVE_IMAGE_BATCH_STATUSES = new Set(['queued', 'pending', 'running']);
export const TERMINAL_IMAGE_BATCH_STATUSES = new Set(['completed', 'error', 'cancelled']);
const TERMINAL_PROCESS_STATUSES = new Set(['complete', 'end', 'error', 'cancelled']);

/**
 * Compare two queue snapshots.
 * @param {Array<object>|null} prev  rows from the previous poll; null before the first
 * @param {Array<object>} next       rows from this poll
 * @returns {{finished: string[], grew: string[], appeared: string[]}} batch ids;
 *          all empty on the first poll, which the mount-time history load covers
 */
export function diffImageQueue(prev, next) {
  const out = { finished: [], grew: [], appeared: [] };
  if (!Array.isArray(prev)) return out;
  const before = new Map(prev.map((q) => [q.batch_id, q]));
  (next || []).forEach((q) => {
    const old = before.get(q.batch_id);
    if (!old) {
      out.appeared.push(q.batch_id);
      return;
    }
    if (TERMINAL_IMAGE_BATCH_STATUSES.has(q.status) && !TERMINAL_IMAGE_BATCH_STATUSES.has(old.status)) {
      out.finished.push(q.batch_id);
    } else if ((q.completed_images ?? 0) > (old.completed_images ?? 0)) {
      out.grew.push(q.batch_id);
    }
  });
  return out;
}

/** True when a queue diff means the gallery and the image library are out of date. */
export function queueDiffNeedsRefresh(diff) {
  return Boolean(diff && (diff.finished.length || diff.grew.length || diff.appeared.length));
}

/**
 * Turns queue polls and progress events into Recent Batches reloads: at most one
 * per `delayMs` window while images land, plus a library notice once a batch has
 * finished (its images enter the library on completion).
 * @param {{reload: () => void, notifyLibrary: () => void, delayMs: number}} opts
 * @returns {{observeQueue: (rows: Array<object>) => object, schedule: (o?: {library?: boolean}) => void,
 *            knownBatchIds: () => Set<string>, cancel: () => void}}
 */
export function createGalleryRefresher({ reload, notifyLibrary, delayMs }) {
  let timer = null;
  let library = false;
  let lastRows = null;
  const schedule = ({ library: lib = false } = {}) => {
    library = library || lib;
    if (timer) return;
    timer = setTimeout(() => {
      const notify = library;
      timer = null;
      library = false;
      reload();
      if (notify) notifyLibrary();
    }, delayMs);
  };
  return {
    schedule,
    /** Feed one queue poll (rows as shown); returns what changed since the last one. */
    observeQueue(rows) {
      const diff = diffImageQueue(lastRows, rows);
      lastRows = rows;
      if (queueDiffNeedsRefresh(diff)) schedule({ library: diff.finished.length > 0 });
      return diff;
    },
    knownBatchIds: () => new Set((lastRows || []).map((q) => q.batch_id)),
    cancel() {
      clearTimeout(timer);
      timer = null;
      library = false;
    },
  };
}

/**
 * The batch to show live when the page has none open: the running one, else the
 * first still waiting in line. Null when nothing is active.
 * @param {Array<object>} rows  queue rows
 * @returns {string|null}
 */
export function batchToResume(rows) {
  const active = (rows || []).filter((q) => ACTIVE_IMAGE_BATCH_STATUSES.has(q.status));
  const running = active.find((q) => q.is_running || q.status === 'running');
  return (running || active[0])?.batch_id ?? null;
}

/**
 * Recent Batches entries with the queue's live status and image count laid over
 * them, so a running batch's card counts up as images land. Entries the queue
 * does not change keep their identity, which keeps the memoised cards still.
 * @param {Array<object>} history  rows from /batch-image/list
 * @param {Array<object>} rows     rows from /batch-image/queue
 * @returns {Array<object>}
 */
export function overlayLiveCounts(history, rows) {
  const live = new Map((rows || []).map((q) => [q.batch_id, q]));
  let changed = false;
  const out = (history || []).map((b) => {
    const q = live.get(b.batch_id);
    if (!q) return b;
    const completed = Math.max(q.completed_images ?? 0, b.completed_images ?? 0);
    if (completed === b.completed_images && q.status === b.status) return b;
    changed = true;
    return { ...b, status: q.status ?? b.status, completed_images: completed };
  });
  return changed ? out : history;
}

/**
 * image_generation progress events the gallery has not acted on yet.
 * @param {Iterable<object>} processes  activeProcesses values from UnifiedProgressContext
 * @param {Set<string>} handled         batch ids whose terminal event was already acted on
 * @param {Set<string>} known           batch ids the queue already lists
 * @returns {{finished: string[], unknown: string[]}} batch ids that just reached a
 *          terminal state, and running ones the queue does not list yet
 */
export function imageProcessEvents(processes, handled, known) {
  const finished = new Set();
  const unknown = new Set();
  for (const p of processes || []) {
    const type = p?.processType || p?.process_type;
    const batchId = p?.additional_data?.batch_id;
    if (type !== 'image_generation' || !batchId) continue;
    if (TERMINAL_PROCESS_STATUSES.has(p.status)) {
      if (!handled.has(batchId)) finished.add(batchId);
    } else if (!known.has(batchId)) {
      unknown.add(batchId);
    }
  }
  return { finished: [...finished], unknown: [...unknown] };
}
