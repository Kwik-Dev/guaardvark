import React from "react";
import { Alert } from "@mui/material";

/**
 * @param {boolean} [gpuBusy] - GPU held or cooling down (preferred)
 * @param {boolean} [gpuBlocked] - Legacy alias for gpuBusy when queueMode is false
 * @param {boolean} [queueMode] - Queue-backed surfaces: informational, not a hard block
 */
export default function GpuGateBanner({
  gpuBusy,
  gpuBlocked,
  blockReason,
  queueMode = false,
}) {
  const busy = gpuBusy ?? gpuBlocked;
  if (!busy || !blockReason) return null;

  if (queueMode) {
    return (
      <Alert severity="info" sx={{ mb: 1 }}>
        GPU in use — {blockReason}. New jobs stack in the queue and run automatically when
        the GPU is free.
      </Alert>
    );
  }

  return (
    <Alert severity="info" sx={{ mb: 1 }}>
      GPU temporarily unavailable — {blockReason}. Actions will unlock when the GPU is free.
    </Alert>
  );
}