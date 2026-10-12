// Reading a bulk import job's status record: the worker's
// (bulk_import_documents_task, polled through /files/bulk-import/<id>/status)
// and the browser upload's, which reports in the same shape.

// The worker ends a job with "complete"; this page once waited for
// "completed" and polled forever. Either spelling ends the job.
const SUCCESS_STATUSES = ["complete", "completed"];
const FAILED_STATUSES = ["error", "failed"];
const TERMINAL_STATUSES = [...SUCCESS_STATUSES, ...FAILED_STATUSES, "cancelled", "stopped"];

export const normalizeStatus = (status) => String(status || "").toLowerCase();
export const isSuccessStatus = (status) => SUCCESS_STATUSES.includes(normalizeStatus(status));
export const isFailedStatus = (status) => FAILED_STATUSES.includes(normalizeStatus(status));
export const isTerminalStatus = (status) => TERMINAL_STATUSES.includes(normalizeStatus(status));

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/**
 * What the rules kept out, in the words of the worker's own status message
 * (_skipped_summary): "1 hidden or internal folder and 2 hidden or private files".
 */
export const skippedPhrase = (skipped) => {
  if (!skipped) return "";
  const folders = (skipped.hidden_dirs || 0) + (skipped.internal_dirs || 0);
  const files = (skipped.hidden_files || 0) + (skipped.sensitive_files || 0) + (skipped.symlinks || 0);
  const parts = [];
  if (folders) parts.push(plural(folders, "hidden or internal folder"));
  if (files) parts.push(plural(files, "hidden or private file"));
  return parts.join(" and ");
};

/** The pill for a job: a label and a StatusPill tone. A finished job with failures is never green. */
export const jobPill = (job) => {
  const status = normalizeStatus(job?.status);
  const errors = Number(job?.stats?.errors) || 0;
  if (isSuccessStatus(status)) {
    if (job.preview) return { label: "Preview ready", tone: "info" };
    return errors > 0
      ? { label: `Finished with errors (${errors} failed)`, tone: "warn" }
      : { label: "Imported", tone: "ok" };
  }
  if (isFailedStatus(status)) return { label: "Failed", tone: "error" };
  if (status === "stopped") return { label: "Stopped", tone: "warn" };
  if (status === "cancelled") return { label: "Cancelled", tone: "neutral" };
  if (status === "uploading") return { label: "Uploading", tone: "info" };
  if (status === "queued") return { label: "Queued", tone: "info" };
  return { label: job?.preview ? "Counting files" : "Importing", tone: "info" };
};

/**
 * One line of results in plain counts, e.g.
 * "298 imported · 4 already there · 0 failed · 298 queued for indexing".
 * A preview says how many files would come in.
 */
export const resultLine = (stats, { preview = false } = {}) => {
  if (!stats) return "";
  const parts = [];
  if (preview) {
    if (stats.total_files != null) parts.push(`${plural(Number(stats.total_files) || 0, "file")} would be imported`);
  } else {
    if (stats.imported != null) parts.push(`${stats.imported} imported`);
    if (stats.skipped) parts.push(`${stats.skipped} already there`);
    if (stats.errors != null) parts.push(`${stats.errors} failed`);
    if (stats.queued_for_indexing) parts.push(`${stats.queued_for_indexing} queued for indexing`);
  }
  const skipped = skippedPhrase(stats.skipped_private);
  if (skipped) parts.push(`skipped ${skipped}`);
  return parts.join(" · ");
};
