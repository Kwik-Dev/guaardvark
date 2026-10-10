import { useState, useEffect, useCallback, useMemo } from "react";
import { getJobsGate } from "../api/jobsService";

const POLL_MS = 4000;

// Plain names for the job holding the card. Batch ids carry their kind as a
// prefix (output_registration.BATES_PREFIXES); other holders are named by the
// JobKind the gate recorded (job_types.JobKind). Mirrors
// job_operation_gate.gpu_holder_label on the backend.
const HOLDER_ID_LABELS = [
  ["VideoBatch", "A video is rendering"],
  ["ImageBatch", "Pictures are rendering"],
  ["blueprint_", "Pictures are rendering"],
  ["upload_", "Pictures are rendering"],
];
const HOLDER_KIND_LABELS = {
  video_render: "A video is rendering",
  video_gen: "A video is rendering",
  music_video: "A music video is rendering",
  production: "The Film Crew is rendering",
  training: "A model is training",
  lora_train: "A character is training",
  batch_csv: "A batch job is running",
};

/**
 * Plain words for a gate holder ({kind, native_id}): what is using the card,
 * and the kind:id pair for a tooltip. Never shows the id in the sentence.
 */
export function describeGpuHolder(holder) {
  if (!holder) return { reason: null, detail: null };
  const id = String(holder.native_id || "");
  const kind = String(holder.kind || "");
  const byId = HOLDER_ID_LABELS.find(([prefix]) => id.startsWith(prefix));
  const reason = byId
    ? byId[1]
    : HOLDER_KIND_LABELS[kind] || "Another job is using the graphics card";
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
      return { blockReason: "Another job is using the graphics card", blockDetail: null };
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