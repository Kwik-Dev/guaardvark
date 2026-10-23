/**
 * Pure helpers for the music-video progress meter. The backend computes the
 * numbers (backend/services/music_video_progress.py); this only maps them to
 * what the page shows. No React, so it unit-tests with plain vitest.
 */

/** MUI colour per stage state. */
export const STAGE_STATE_COLOR = {
  done: "success",
  active: "primary",
  queued: "warning",
  waiting: "warning",
  failed: "error",
  cancelled: "default",
  pending: "default",
  skipped: "default",
};

export const STAGE_STATE_TEXT = {
  done: "Done",
  active: "Running",
  queued: "Waiting for GPU",
  waiting: "Waiting for you",
  failed: "Failed",
  cancelled: "Cancelled",
  pending: "Not started",
  skipped: "Skipped",
};

/** "1h 04m", "12m 30s", "45s"; "" for null/negative. */
export function formatDuration(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds)) || Number(seconds) < 0) return "";
  const s = Math.round(Number(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m ${String(sec).padStart(2, "0")}s`;
  return `${sec}s`;
}

/** The ETA line: "~12m 30s left (measured)" / "" when unknown. */
export function etaText(progress) {
  if (!progress || progress.eta_seconds == null) return "";
  const basis = progress.eta_basis === "measured" ? "measured" : "estimate";
  return `~${formatDuration(progress.eta_seconds)} left (${basis})`;
}

/** State of one clip cell in the strip. */
export function clipCellState(clip, currentIndex) {
  if (!clip) return "pending";
  if (clip.status === "cancelled") return "cancelled";
  if (clip.status === "failed") return "failed";
  const review = clip.review && clip.review.state;
  if (clip.status === "done" && review === "needs_review") return "held";
  if (clip.status === "done") return "done";
  if (currentIndex != null && clip.index === currentIndex) return "rendering";
  return "pending";
}

export const CLIP_CELL_COLOR = {
  done: "success.main",
  held: "warning.main",
  failed: "error.main",
  rendering: "info.main",
  cancelled: "action.disabled",
  pending: "action.hover",
};

/** One line describing the clip in flight: "Clip 6/12 · Animating (I2V) 14/30". */
export function currentClipText(progress) {
  const cur = progress && progress.current_clip;
  const gen = progress && (progress.stages || []).find((s) => s.key === "generating");
  if (!cur || !gen || !gen.detail) return "";
  const total = gen.detail.clip_count || 0;
  let text = `Clip ${cur.position || (cur.index ?? 0) + 1}/${total}`;
  if (cur.substage_label) text += ` · ${cur.substage_label}`;
  if (cur.steps) text += ` ${cur.step ?? 0}/${cur.steps}`;
  if (cur.queued) text += " · waiting for GPU";
  return text;
}

/** Stage rows to draw: drop "skipped" ones so the stepper stays short. */
export function visibleStages(progress) {
  return ((progress && progress.stages) || []).filter((s) => s.state !== "skipped");
}

/** Index of the stage to highlight in the stepper. */
export function activeStepIndex(stages) {
  const i = stages.findIndex((s) => ["active", "queued", "waiting", "failed", "cancelled"].includes(s.state));
  if (i >= 0) return i;
  return stages.every((s) => s.state === "done") ? stages.length : 0;
}
