import { useState, useEffect, useCallback, useMemo } from "react";
import { getJobsGate } from "../api/jobsService";

const POLL_MS = 4000;

/**
 * Poll GET /api/jobs/gate so GPU-heavy pages can disable actions while the
 * exclusive GPU slot is held or cooling down.
 */
export default function useJobsGate({ enabled = true, pollMs = POLL_MS } = {}) {
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

  const gpuBlocked = Boolean(
    gate?.gpu_busy || (gate?.gpu_cooldown_remaining_s ?? 0) > 0
  );

  const blockReason = useMemo(() => {
    if (!gate) return null;
    const cooldown = gate.gpu_cooldown_remaining_s ?? 0;
    if (cooldown > 0) {
      return `GPU cooling down (${Math.ceil(cooldown)}s)`;
    }
    if (gate.gpu_busy && gate.gpu_holder) {
      const h = gate.gpu_holder;
      const id = h.native_id ? ` (${h.native_id})` : "";
      return `GPU in use: ${h.kind}${id}`;
    }
    if (gate.gpu_busy) return "GPU in use by another job";
    return null;
  }, [gate]);

  return { gate, loading, gpuBlocked, blockReason, refresh };
}