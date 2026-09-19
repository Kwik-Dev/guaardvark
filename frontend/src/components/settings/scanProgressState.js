// Self-check scan UI phases. Cancel used to only stop polling; the modal now
// waits on POST /api/self-improvement/scans/<id>/cancel until the existing
// status/runs routes report cancelled (or the scan completes first).

export function resolveScanId(triggerData, run) {
  return (
    triggerData?.scan_id ??
    triggerData?.id ??
    run?.scan_id ??
    run?.id ??
    triggerData?.task_id ??
    null
  );
}

export function scanStatusFromPayload(statusPayload, run) {
  const data = statusPayload?.data ?? statusPayload ?? {};
  const candidates = [data.status, data.scan?.status, data.current_scan?.status];
  for (const value of candidates) {
    if (typeof value === "string" && value) return value;
  }
  const last = data.last_run;
  if (last && typeof last.status === "string") {
    if (run?.id == null || last.id == null || last.id === run.id) return last.status;
  }
  if (typeof run?.status === "string" && run.status) return run.status;
  return null;
}

export function scanUiPhase({ cancelRequested, runStatus, liveStage }) {
  const status = String(runStatus || "").toLowerCase();
  const stage = String(liveStage || "").toLowerCase();
  if (status === "cancelled" || stage === "cancelled") return "cancelled";
  if (status === "success" || stage === "complete" || status === "completed") return "completed";
  if (status === "failed" || stage === "error") return "failed";
  if (status === "blocked_by_guardian") return "blocked";
  if (cancelRequested) return "cancelling";
  return "running";
}

export function isScanTerminal(phase) {
  return phase === "cancelled" || phase === "completed" || phase === "failed" || phase === "blocked";
}
