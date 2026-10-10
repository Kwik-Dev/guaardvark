import { useState, useEffect, useCallback, useMemo } from "react";
import { getJobsGate } from "../api/jobsService";

const POLL_MS = 4000;

// Plain words for the job holding the card. video_render is the general
// GPU-exclusive kind (stills, edits, songs and storyboards claim it too), so the
// kind alone does not say what is rendering; the native id does. Batch ids carry
// their kind as a prefix (output_registration.BATES_PREFIXES); the other prefixes
// are the ids each caller passes to gpu_session. Mirrors
// job_operation_gate._HOLDER_ID_LABELS on the backend.
const HOLDER_ID_LABELS = [
  ["VideoBatch", "A video is rendering"],
  ["ImageBatch", "Pictures are rendering"],
  ["blueprint_", "Pictures are rendering"],
  ["upload_", "Pictures are rendering"],
  ["stills_", "A picture is rendering"],
  ["gen_", "A picture is rendering"],
  ["chat_edit_", "A picture is being edited"],
  ["chat_anim_", "An animation is rendering"],
  ["infographic_", "An infographic is rendering"],
  ["char_", "Character pictures are rendering"],
  ["storyboard_", "Storyboard pictures are rendering"],
  ["prod_", "The Film Crew is rendering"],
  ["mv_", "A music video is rendering"],
  ["video:music3:", "A song is rendering"],
  ["editor_", "A video is rendering"],
  ["router:", "A video is rendering"],
];
// Only the kinds that can hold the card (job_operation_gate.GPU_EXCLUSIVE_KINDS)
// and say what they are by themselves; video_render falls to the generic line.
const HOLDER_KIND_LABELS = {
  training: "A model is training",
  lora_train: "A character is training",
};
const GENERIC_HOLDER = "Something is using the graphics card";

/**
 * Plain words for a gate holder ({kind, native_id}): what is using the card,
 * and the kind:id pair for a tooltip. Never shows the id in the sentence.
 */
export function describeGpuHolder(holder) {
  if (!holder) return { reason: null, detail: null };
  const id = String(holder.native_id || "");
  const kind = String(holder.kind || "");
  const byId = HOLDER_ID_LABELS.find(([prefix]) => id.startsWith(prefix));
  const reason = byId ? byId[1] : HOLDER_KIND_LABELS[kind] || GENERIC_HOLDER;
  const detail = [kind, id].filter(Boolean).join(":") || null;
  return { reason, detail };
}

/**
 * Poll GET /api/jobs/gate so GPU-heavy pages can reflect GPU occupancy.
 *
 * submitMode:
 *   - "immediate" (default): gpuSubmitBlocked while GPU is held or cooling down.
 *     Use for ops that need the GPU right now (editor render, inline storyboard gen).
 *   - "queue": submission is never blocked — the backend stacks jobs and the worker
 *     waits for the GPU (batch video, Celery-dispatched pipelines). gpuBusy stays
 *     true for informational banners and for disabling immediate GPU actions.
 */
export default function useJobsGate({
  enabled = true,
  pollMs = POLL_MS,
  submitMode = "immediate",
} = {}) {
  const [gate, setGate] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await getJobsGate();
      setGate(data);
    } catch {
      // Keep the last known snapshot on transient failures.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return undefined;
    refresh();
    const id = setInterval(refresh, pollMs);
    return () => clearInterval(id);
  }, [enabled, pollMs, refresh]);

  const gpuBusy = Boolean(
    gate?.gpu_busy || (gate?.gpu_cooldown_remaining_s ?? 0) > 0
  );

  const gpuSubmitBlocked = submitMode === "queue" ? false : gpuBusy;
  // Backward compat — existing callers treat gpuBlocked as "disable submit".
  const gpuBlocked = gpuSubmitBlocked;

  // blockReason is a plain sentence for the banner; blockDetail is the
  // holder's kind and id for a tooltip, never printed in the sentence.
  const { blockReason, blockDetail } = useMemo(() => {
    if (!gate) return { blockReason: null, blockDetail: null };
    const cooldown = gate.gpu_cooldown_remaining_s ?? 0;
    if (cooldown > 0) {
      return {
        blockReason: `The graphics card is clearing up after the last job (${Math.ceil(cooldown)} s)`,
        blockDetail: null,
      };
    }
    if (gate.gpu_busy && gate.gpu_holder) {
      const { reason, detail } = describeGpuHolder(gate.gpu_holder);
      return { blockReason: reason, blockDetail: detail };
    }
    if (gate.gpu_busy) {
      return { blockReason: GENERIC_HOLDER, blockDetail: null };
    }
    return { blockReason: null, blockDetail: null };
  }, [gate]);

  return {
    gate,
    loading,
    gpuBusy,
    gpuSubmitBlocked,
    gpuBlocked,
    blockReason,
    blockDetail,
    refresh,
  };
}