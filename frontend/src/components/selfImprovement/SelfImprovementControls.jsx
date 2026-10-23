// Self-improvement's switches, the self-check, and the pending fixes: the
// Self-improvement page's controls (they lived in Settings → Agents before).
import React, { useState, useEffect, useCallback } from "react";
import { Box } from "@mui/material";
import { PlayArrow as PlayIcon } from "@mui/icons-material";
import FixesModal from "../settings/FixesModal";
import ScanProgressModal from "../settings/ScanProgressModal";
import { ActionButton, Cluster, Hint, Line, Sep, SettingChip, StatusPill } from "../settings/ui";
import { selfImprovementService } from "../../api/selfImprovementService";

export default function SelfImprovementControls() {
  const [siStatus, setSiStatus] = useState(null);
  const [waitingFixes, setWaitingFixes] = useState(null);
  const [scanOpen, setScanOpen] = useState(false);
  const [scanInBackground, setScanInBackground] = useState(false);
  const [scanCancelled, setScanCancelled] = useState(false);
  const [fixesOpen, setFixesOpen] = useState(false);

  const fetchStatus = useCallback(async () => {
    const [statusRes, fixesRes] = await Promise.allSettled([
      selfImprovementService.getStatus(),
      selfImprovementService.countWaitingFixes(),
    ]);
    if (statusRes.status === "fulfilled") setSiStatus(statusRes.value?.data);
    if (fixesRes.status === "fulfilled") setWaitingFixes(Number(fixesRes.value?.data?.waiting) || 0);
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handleToggleSelfImprovement = async () => {
    try {
      await selfImprovementService.toggle(!siStatus?.enabled);
      fetchStatus();
    } catch (err) {
      console.error("Failed to toggle self-improvement:", err);
    }
  };

  const handleToggleCodebaseLock = async () => {
    try {
      await selfImprovementService.lockCodebase(!siStatus?.codebase_locked);
      fetchStatus();
    } catch (err) {
      console.error("Failed to toggle codebase lock:", err);
    }
  };

  const handleOpenScan = () => {
    setScanInBackground(false);
    setScanCancelled(false);
    setScanOpen(true);
  };

  // Stable reference — the modal depends on this in its main effect, and a
  // fresh closure on every render would re-dispatch the scan.
  const handleScanComplete = useCallback((run) => {
    fetchStatus();
    setScanInBackground(false);
    if (run?.status === "cancelled") {
      setScanCancelled(true);
      return;
    }
    const proposedAnything =
      (run?.changes_made && run.changes_made.length > 0) ||
      run?.status === "success";
    if (proposedAnything) {
      setScanOpen(false);
      setFixesOpen(true);
    }
  }, [fetchStatus]);

  const runs = siStatus?.total_fixes || 0;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.25 }}>
      <Cluster
        label="Self-improvement"
        help="Guaardvark checks its own code and proposes fixes for you to review. Codebase locked blocks every automatic edit."
      >
        <Line>
          <SettingChip
            label="Self-improvement"
            on={!!siStatus?.enabled}
            onToggle={handleToggleSelfImprovement}
            disabled={!siStatus}
            tooltip="Lets the mentor scan the codebase and propose fixes for review."
          />
          <SettingChip
            label="Codebase locked"
            on={!!siStatus?.codebase_locked}
            onToggle={handleToggleCodebaseLock}
            disabled={!siStatus}
            tooltip={siStatus?.codebase_locked ? "Autonomous edits are blocked. Turn off to allow them." : "Turn on to block every autonomous edit to the source tree."}
          />
          <Sep />
          <ActionButton
            onClick={handleOpenScan}
            loading={scanOpen}
            disabled={!siStatus?.enabled || !!siStatus?.codebase_locked}
            startIcon={<PlayIcon />}
            tooltip={
              !siStatus?.enabled
                ? "Turn on self-improvement first"
                : siStatus?.codebase_locked
                  ? "Unlock the codebase first"
                  : "Runs the checks now. Cancel asks the server to stop; closing the view leaves the scan running."
            }
          >
            Run self-check
          </ActionButton>
        </Line>
        <Hint>
          {siStatus?.last_run
            ? `Last run ${new Date(siStatus.last_run.timestamp).toLocaleString()} (${siStatus.last_run.status}) · ${runs} successful run${runs === 1 ? "" : "s"}.`
            : siStatus
              ? "No runs yet."
              : "Reading the status…"}
          {siStatus?.codebase_locked ? " Codebase is locked: autonomous edits are blocked." : ""}
        </Hint>
        {scanCancelled && (
          <StatusPill tone="neutral" label="Cancelled" />
        )}
        {scanInBackground && !scanCancelled && (
          <StatusPill tone="info" label="Scan continues in the background" />
        )}
      </Cluster>

      <Cluster
        label="Pending fixes"
        help="Fixes the self-check proposed, waiting for you to review or apply. Nothing reaches the code until you approve and apply it."
      >
        <Line>
          <StatusPill
            tone={waitingFixes ? "warn" : "neutral"}
            label={waitingFixes == null ? "…" : `${waitingFixes} waiting`}
          />
          <ActionButton onClick={() => setFixesOpen(true)}>
            Review fixes
          </ActionButton>
        </Line>
      </Cluster>

      <ScanProgressModal
        open={scanOpen}
        onClose={() => setScanOpen(false)}
        onBackground={() => setScanInBackground(true)}
        onComplete={handleScanComplete}
      />
      <FixesModal
        open={fixesOpen}
        onClose={() => {
          setFixesOpen(false);
          fetchStatus();
        }}
      />
    </Box>
  );
}
