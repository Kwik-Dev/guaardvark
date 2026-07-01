import React from "react";
import { Alert } from "@mui/material";

export default function GpuGateBanner({ gpuBlocked, blockReason }) {
  if (!gpuBlocked || !blockReason) return null;
  return (
    <Alert severity="info" sx={{ mb: 1 }}>
      GPU temporarily unavailable — {blockReason}. Actions will unlock when the GPU is free.
    </Alert>
  );
}