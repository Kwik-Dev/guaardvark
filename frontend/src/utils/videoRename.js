// Applying a clip rename (PUT /batch-video/video/<batch>/<path>/rename) to page state.

/**
 * The file-name part of a clip path; the rename prompt shows only this.
 * @param {string} videoPath
 * @returns {string}
 */
export function clipFileName(videoPath) {
  return String(videoPath || "").split(/[\\/]/).filter(Boolean).pop() || "";
}

function normalise(path) {
  return String(path || "").replace(/\\/g, "/").replace(/^(\.\/|\/)+/, "");
}

function sameClip(path, oldPath) {
  const a = normalise(path);
  const b = normalise(oldPath);
  return Boolean(a && b) && (a === b || a.endsWith(`/${b}`));
}

function patchResult(result, renamed) {
  return {
    ...result,
    video_path: renamed.video_path,
    thumbnail_path: renamed.thumbnail_path ?? result.thumbnail_path,
    frame_paths: (result.frame_paths || []).map((p) =>
      sameClip(p, renamed.old_video_path) ? renamed.video_path : p,
    ),
  };
}

/**
 * Batch status with the renamed clip's paths swapped in.
 * @param {object|null} status   GET /batch-video/status payload shown on the page
 * @param {string} batchId
 * @param {{old_video_path: string, video_path: string, thumbnail_path: ?string}} renamed
 *        the rename route's answer
 * @returns {object|null} a new object when a result matched, else `status` itself
 */
export function applyClipRename(status, batchId, renamed) {
  if (!status || status.batch_id !== batchId || !renamed?.video_path) return status;
  let matched = false;
  const results = (status.results || []).map((r) => {
    if (!sameClip(r.video_path, renamed.old_video_path)) return r;
    matched = true;
    return patchResult(r, renamed);
  });
  return matched ? { ...status, results } : status;
}

/**
 * Open player state with the renamed clip repointed; the current clip's url and
 * title follow it.
 * @param {object|null} player  {url, title, batchId, results, currentIndex}
 * @param {string} batchId
 * @param {object} renamed      the rename route's answer
 * @param {(videoPath: string) => string} urlFor  builds the URL that serves a clip
 * @returns {object|null}
 */
export function applyClipRenameToPlayer(player, batchId, renamed, urlFor) {
  if (!player || player.batchId !== batchId || !renamed?.video_path) return player;
  let matched = false;
  let current = null;
  const results = (player.results || []).map((r, i) => {
    if (!sameClip(r.video_path, renamed.old_video_path)) return r;
    matched = true;
    const next = patchResult(r, renamed);
    if (i === player.currentIndex) current = next;
    return next;
  });
  if (!matched) return player;
  if (!current) return { ...player, results };
  return {
    ...player,
    results,
    url: urlFor(current.video_path),
    title: clipFileName(current.video_path),
  };
}
