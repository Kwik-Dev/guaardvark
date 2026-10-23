import React, { useState, useEffect, useCallback } from "react";
import {
  Alert,
  Box,
  LinearProgress,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  CircularProgress,
} from "@mui/material";
import { PlayArrow as PlayIcon } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import CollapsibleAlert from "../common/CollapsibleAlert";
import { UNCLE_GOLD } from "../../utils/familyColors";
import { ActionButton, Cluster, Hint, Line, Sep, SettingChip, StatusPill } from "./ui";
import { claudeAdvisorService } from "../../api/claudeAdvisorService";
import { formatUiError } from "../../utils/uiError";

export default function UncleClaudeSection() {
  const navigate = useNavigate();
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  // A failed status read or config save, said under the controls. Without it
  // a failed read showed "Not configured" and a failed save did nothing.
  const [failure, setFailure] = useState(null);

  const fetchStatus = useCallback(async () => {
    try {
      const [claudeRes] = await Promise.allSettled([claudeAdvisorService.getStatus()]);
      if (claudeRes.status === "fulfilled") {
        setStatus(claudeRes.value?.data);
      } else {
        setFailure(`Uncle Claude's status could not be read: ${formatUiError(claudeRes.reason) || "the request failed"}.`);
      }
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
      setFailure(null);
      await claudeAdvisorService.updateConfig({ escalation_mode: e.target.value });
      fetchStatus();
    } catch (err) {
      console.error("Failed to update escalation mode:", err);
      setFailure(`Escalation was not changed: ${formatUiError(err) || "the request failed"}.`);
    }
  };

  const handleToggleScheduledSends = async (next) => {
    try {
      setFailure(null);
      await claudeAdvisorService.updateConfig({ scheduled_sends: next });
      fetchStatus();
    } catch (err) {
      console.error("Failed to update scheduled sends:", err);
      setFailure(`Scheduled sends were not changed: ${formatUiError(err) || "the request failed"}.`);
    }
  };

  if (loading) {
    return <CircularProgress size={16} />;
  }

  const usage = status?.usage || {};
  const budgetPercent = usage.budget_used_percent || 0;
  const connection = !status && failure
    ? { tone: "error", label: "Status unknown" }
    : !status?.available
    ? { tone: "warn", label: "Not configured" }
    : testResult === null
      ? { tone: "ok", label: "API key set" }
      : testResult.success
        ? { tone: "ok", label: "Verified" }
        : { tone: "error", label: "Connection failed" };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.25 }}>
      <Cluster
        label="Uncle Claude"
        help="An optional mentor: Guaardvark asks Anthropic's Claude for help over the internet. It needs your own Anthropic API key, and every request counts against the monthly token budget below."
      >
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
          <SettingChip
            label="Scheduled sends"
            on={!!status?.scheduled_sends}
            onToggle={handleToggleScheduledSends}
            disabled={!status?.available}
            tooltip="On: Uncle Claude contacts Anthropic on its own schedule — advice twice a day (time and GPU details) and reviews of servo changes (a source excerpt and the diff). Off: it only runs when you start it here."
          />
        </Line>
        {failure && (
          <Alert severity="error" sx={{ py: 0.25 }} onClose={() => setFailure(null)}>
            {failure}
          </Alert>
        )}
        {testResult && (
          <CollapsibleAlert severity={testResult.success ? "success" : "error"} sx={{ py: 0.25 }} onClose={() => setTestResult(null)}>
            {testResult.message}
          </CollapsibleAlert>
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

      <Cluster
        label="Self-improvement"
        help="Guaardvark checks its own code and proposes fixes for you to review. Its switches, the self-check, pending fixes and the run log have their own page."
      >
        <Line>
          <ActionButton onClick={() => navigate("/self-improvement")}>
            Open Self-improvement
          </ActionButton>
        </Line>
      </Cluster>
    </Box>
  );
}
