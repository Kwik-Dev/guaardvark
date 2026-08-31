import React, { useState, useEffect, useCallback } from "react";
import {
  Box,
  LinearProgress,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Alert,
  CircularProgress,
} from "@mui/material";
import { PlayArrow as PlayIcon } from "@mui/icons-material";
import FixesModal from "./FixesModal";
import ScanProgressModal from "./ScanProgressModal";
import { UNCLE_GOLD } from "../../utils/familyColors";
import { ActionButton, Cluster, Hint, Line, Sep, SettingChip, StatusPill } from "./ui";
import { claudeAdvisorService } from "../../api/claudeAdvisorService";
import { selfImprovementService } from "../../api/selfImprovementService";

export default function UncleClaudeSection() {
  const [status, setStatus] = useState(null);
  const [siStatus, setSiStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [scanOpen, setScanOpen] = useState(false);
  const [fixesOpen, setFixesOpen] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const [claudeRes, siRes] = await Promise.allSettled([
        claudeAdvisorService.getStatus(),
        selfImprovementService.getStatus(),
      ]);
      if (claudeRes.status === "fulfilled") setStatus(claudeRes.value?.data);
      if (siRes.status === "fulfilled") setSiStatus(siRes.value?.data);
    } catch (err) {
      console.error("Failed to fetch Uncle Claude status:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await claudeAdvisorService.testConnection();
      setTestResult({ success: true, message: res?.data?.response || "Connected" });
    } catch (err) {
      setTestResult({ success: false, message: err.message || "Connection failed" });
    } finally {
      setTesting(false);
    }
  };

  const handleEscalationModeChange = async (e) => {
    try {
      await claudeAdvisorService.updateConfig({ escalation_mode: e.target.value });
      fetchStatus();
    } catch (err) {
      console.error("Failed to update escalation mode:", err);
    }
  };

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
    setScanOpen(true);
  };

  // Stable reference — the modal depends on this in its main effect, and a
  // fresh closure on every render would re-dispatch the scan.
  const handleScanComplete = useCallback((run) => {
    fetchStatus();
    const proposedAnything =
      (run?.changes_made && run.changes_made.length > 0) ||
      run?.status === "success";
    if (proposedAnything) {
      setScanOpen(false);
      setFixesOpen(true);
    }
  }, [fetchStatus]);

  if (loading) {
    return <CircularProgress size={16} />;
  }

  const usage = status?.usage || {};
  const budgetPercent = usage.budget_used_percent || 0;
  const runs = siStatus?.total_fixes || 0;
  const connection = !status?.available
    ? { tone: "warn", label: "Not configured" }
    : testResult === null
      ? { tone: "ok", label: "API key set" }
      : testResult.success
        ? { tone: "ok", label: "Verified" }
        : { tone: "error", label: "Connection failed" };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.25 }}>
      <Cluster label="Uncle Claude" note="mentor API">
        <Line>
          <StatusPill tone={connection.tone} label={connection.label} />
          {status?.model && <Hint>{status.model}</Hint>}
          <ActionButton
            onClick={handleTestConnection}
            loading={testing}
            disabled={!status?.available}
            startIcon={<PlayIcon />}
            tooltip="Sends one request to Anthropic. It counts against the token budget."
          >
            Test connection
          </ActionButton>
          <Sep />
          <FormControl size="small" sx={{ minWidth: 210 }}>
            <InputLabel id="uncle-escalation-label">Escalation</InputLabel>
            <Select
              labelId="uncle-escalation-label"
              label="Escalation"
              value={status?.escalation_mode || "manual"}
              onChange={handleEscalationModeChange}
            >
              <MenuItem value="manual">Manual (user triggers)</MenuItem>
              <MenuItem value="smart">Smart (auto when local fails)</MenuItem>
              <MenuItem value="always">Always (every query, paid)</MenuItem>
            </Select>
          </FormControl>
        </Line>
        {testResult && (
          <Alert severity={testResult.success ? "success" : "error"} sx={{ py: 0.25 }} onClose={() => setTestResult(null)}>
            {testResult.message}
          </Alert>
        )}
        <Line>
          <Hint>
            {(usage.total_tokens || 0).toLocaleString()} / {(usage.monthly_budget || 0).toLocaleString()} tokens ·{" "}
            {budgetPercent}% used
          </Hint>
          <LinearProgress
            variant="determinate"
            value={Math.min(budgetPercent, 100)}
            sx={{
              width: 140,
              height: 5,
              borderRadius: 3,
              bgcolor: "action.hover",
              "& .MuiLinearProgress-bar": {
                bgcolor: budgetPercent > 80 ? "error.main" : budgetPercent > 50 ? "warning.main" : UNCLE_GOLD,
              },
            }}
          />
        </Line>
      </Cluster>

      <Cluster label="Self-improvement">
        <Line>
          <SettingChip
            label="Self-improvement"
            on={!!siStatus?.enabled}
            onToggle={handleToggleSelfImprovement}
            tooltip="Lets the mentor scan the codebase and propose fixes for review."
          />
          <SettingChip
            label="Codebase locked"
            on={!!siStatus?.codebase_locked}
            onToggle={handleToggleCodebaseLock}
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
                  : "Runs the checks now; takes a few minutes and cannot be cancelled."
            }
          >
            Run self-check
          </ActionButton>
          <ActionButton kind="link" onClick={() => setFixesOpen(true)}>
            Review fixes
          </ActionButton>
        </Line>
        <Hint>
          {siStatus?.last_run
            ? `Last run ${new Date(siStatus.last_run.timestamp).toLocaleString()} (${siStatus.last_run.status}) · ${runs} successful run${runs === 1 ? "" : "s"}.`
            : "No runs yet."}
          {siStatus?.codebase_locked ? " Codebase is locked: autonomous edits are blocked." : ""}
        </Hint>
      </Cluster>

      <ScanProgressModal
        open={scanOpen}
        onClose={() => setScanOpen(false)}
        onComplete={handleScanComplete}
      />
      <FixesModal
        open={fixesOpen}
        onClose={() => setFixesOpen(false)}
      />
    </Box>
  );
}
