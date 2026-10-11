import React from "react";
import { Alert, Tooltip } from "@mui/material";

/**
 * @param {boolean} [gpuBusy] - GPU held or cooling down (preferred)
 * @param {boolean} [gpuBlocked] - Legacy alias for gpuBusy when queueMode is false
 * @param {string}  [blockReason] - plain sentence from useJobsGate ("A video is rendering")
 * @param {string}  [blockDetail] - the holder's kind and id; shown only in a tooltip
 * @param {boolean} [queueMode] - Queue-backed surfaces: informational, not a hard block
 */
export default function GpuGateBanner({
  gpuBusy,
  gpuBlocked,
  blockReason,
  blockDetail,
  queueMode = false,
}) {
  const busy = gpuBusy ?? gpuBlocked;
  if (!busy || !blockReason) return null;

  const text = queueMode
    ? `${blockReason}. New jobs wait in line and start on their own when the graphics card is free.`
    : `${blockReason}. Buttons unlock when the graphics card is free.`;

  const alert = (
    <Alert severity="info" sx={{ mb: 1 }}>
      {text}
    </Alert>
  );
  if (!blockDetail) return alert;
  return (
    <Tooltip title={`Job: ${blockDetail}`} arrow>
      {alert}
    </Tooltip>
  );
}