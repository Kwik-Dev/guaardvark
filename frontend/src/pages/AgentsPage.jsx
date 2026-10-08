// frontend/src/pages/AgentsPage.jsx
// The built-in agents as small tiles in four groups. A click opens the editor;
// a right-click offers Edit, Test, Enable/Disable, Copy id and Reset.
/* eslint-env browser */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import PageLayout from "../components/layout/PageLayout";
import CollapsibleAlertSnackbar from "../components/common/CollapsibleAlertSnackbar";
import {
  Alert,
  Box,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
} from "@mui/material";
import CollapsibleAlert from "../components/common/CollapsibleAlert";
import {
  Refresh,
  PlayArrow,
  SmartToyOutlined,
  CheckCircle,
  Error as ErrorIcon,
} from "@mui/icons-material";
import { alpha } from "@mui/material/styles";
import EmptyState from "../components/common/EmptyState";
import EntityContextMenu from "../components/common/EntityContextMenu";
import { ContextualLoader } from "../components/common/LoadingStates";
import { ActionButton, Cluster } from "../components/settings/ui";
import AgentTile, { agentEdits } from "../components/agents/AgentTile";
import AgentEditDialog, { ResetAgentDialog } from "../components/agents/AgentEditDialog";
import useContextMenu from "../hooks/useContextMenu";
import { executeAgent, getAgents, resetAgent, toggleAgent } from "../api/agentsService";
import { createPlan } from "../api/orchestratorService";
import { useStatus } from "../contexts/StatusContext";

const GROUPS = [
  {
    key: "create",
    label: "Create",
    help: "Agents that write pages, code and data files.",
    order: ["content_creator", "code_assistant", "data_analyst"],
  },
  {
    key: "web",
    label: "Web",
    help: "Agents that search, read and drive web pages. They need web access on in Settings.",
    order: ["research_agent", "browser_automation"],
  },
  {
    key: "computer",
    label: "This computer",
    help: "Agents that act on this machine: the agent's own screen, the desktop and media playback.",
    order: ["agent_vision_control", "desktop_automation", "media_control"],
  },
  {
    key: "routing",
    label: "Routing",
    help: "General Assistant takes what no specialist matches; Task Orchestrator splits a multi-step request across agents.",
    order: ["general_assistant", "orchestrator_agent"],
  },
];

const isOrchestrator = (agent) => agent?.agent_type === "orchestrator";

function groupAgents(agents) {
  const known = new Set(GROUPS.map((g) => g.key));
  const groups = GROUPS.map((group) => ({
    ...group,
    agents: agents
      .filter((a) => a.group === group.key)
      .sort((a, b) => {
        const ia = group.order.indexOf(a.id);
        const ib = group.order.indexOf(b.id);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
      }),
  }));
  const other = agents.filter((a) => !known.has(a.group));
  if (other.length) groups.push({ key: "other", label: "Other", help: "", agents: other });
  return groups.filter((g) => g.agents.length > 0);
}


// Small fixed-width tiles, like the Interconnector tile on Settings: they wrap
// instead of stretching across the row when a group has only one or two agents.
const TILE_GRID = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(200px, 240px))",
  gap: 1.25,
};

const AgentsPage = () => {
  const { activeModel, isLoadingModel, modelError } = useStatus();
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: "", severity: "info" });

  const [editingId, setEditingId] = useState(null);
  const [resetTarget, setResetTarget] = useState(null);
  const [resetting, setResetting] = useState(false);

  const [testAgent, setTestAgent] = useState(null);
  const [testMessage, setTestMessage] = useState("");
  const [testContextJson, setTestContextJson] = useState("{}");
  const [testRunning, setTestRunning] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const menu = useContextMenu();

  const notify = useCallback((message, severity = "success") => {
    setSnackbar({ open: true, message, severity });
  }, []);
  const notifyError = useCallback((message) => notify(message, "error"), [notify]);

  const loadAgents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await getAgents();
      if (response?.success) {
        setAgents(response.agents || []);
      } else {
        setError(response?.error || "Failed to load agents");
      }
    } catch (err) {
      setError(err?.message || "Failed to load agents");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAgents();
  }, [loadAgents]);

  const groups = useMemo(() => groupAgents(agents), [agents]);
  const editing = agents.find((a) => a.id === editingId) || null;

  const replaceAgent = useCallback((updated) => {
    if (!updated?.id) return;
    // The list entry has no tools_detail; keep the shape GET /api/agents returns.
    const { tools_detail: _detail, ...entry } = updated;
    setAgents((prev) => prev.map((a) => (a.id === entry.id ? { ...a, ...entry } : a)));
  }, []);

  const handleToggle = useCallback(
    async (agent) => {
      try {
        const res = await toggleAgent(agent.id);
        if (res?.success) {
          if (res.agent) replaceAgent(res.agent);
          else setAgents((prev) => prev.map((a) => (a.id === agent.id ? { ...a, enabled: res.enabled } : a)));
          notify(`${agent.name} ${res.enabled ? "enabled" : "disabled"}`);
        } else {
          notifyError(res?.error || "Toggle failed");
        }
      } catch (err) {
        notifyError(err?.message || "Toggle failed");
      }
    },
    [notify, notifyError, replaceAgent],
  );

  const copyId = useCallback(
    async (agent) => {
      try {
        await navigator.clipboard.writeText(agent.id);
        notify(`Copied ${agent.id}`);
      } catch (_err) {
        notifyError("Could not copy to the clipboard");
      }
    },
    [notify, notifyError],
  );

  const confirmReset = async () => {
    if (!resetTarget) return;
    setResetting(true);
    try {
      const res = await resetAgent(resetTarget.id);
      if (res?.success) {
        replaceAgent(res.agent);
        notify(`${resetTarget.name} reset to default`);
        setResetTarget(null);
      } else {
        notifyError(res?.error || "Reset failed");
      }
    } catch (err) {
      notifyError(err?.message || "Reset failed");
    } finally {
      setResetting(false);
    }
  };

  const openTest = useCallback((agent) => {
    setTestAgent(agent);
    setTestMessage("");
    setTestContextJson("{}");
    setTestResult(null);
  }, []);

  const runTest = async () => {
    if (!testAgent) return;
    let context = {};
    try {
      context = testContextJson?.trim() ? JSON.parse(testContextJson) : {};
    } catch (_e) {
      setTestResult({ success: false, error: "Context must be valid JSON" });
      return;
    }
    setTestRunning(true);
    setTestResult(null);
    try {
      // The orchestrator does not run through /agents/execute; a test shows the plan it would make.
      const res = isOrchestrator(testAgent)
        ? await createPlan(testMessage, context)
        : await executeAgent({ agent_id: testAgent.id, message: testMessage, context });
      setTestResult(res);
    } catch (err) {
      setTestResult({ success: false, error: err?.message || "Agent execution failed" });
    } finally {
      setTestRunning(false);
    }
  };

  const testOk = testResult?.success && (isOrchestrator(testAgent) || testResult?.result?.success);

  const menuActions = (agent) => {
    if (!agent) return [];
    const edits = agentEdits(agent);
    return [
      { label: "Edit", onClick: () => setEditingId(agent.id) },
      { label: "Test", onClick: () => openTest(agent) },
      { label: agent.enabled ? "Disable" : "Enable", onClick: () => handleToggle(agent) },
      { label: "Copy id", onClick: () => copyId(agent) },
      {
        label: "Reset to default",
        dividerBefore: true,
        disabled: edits.length === 0,
        onClick: () => setResetTarget(agent),
      },
    ];
  };

  return (
    <PageLayout
      title="Agents"
      variant="standard"
      actions={
        <ActionButton startIcon={<Refresh />} onClick={loadAgents} disabled={loading}>
          Refresh
        </ActionButton>
      }
      modelStatus
      activeModel={isLoadingModel ? "Loading..." : modelError ? "Error" : activeModel}
    >
      {error && (
        <CollapsibleAlert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </CollapsibleAlert>
      )}

      {loading && agents.length === 0 ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <ContextualLoader loading message="Loading agents..." showProgress={false} inline />
        </Box>
      ) : agents.length === 0 ? (
        <EmptyState
          icon={<SmartToyOutlined />}
          title="No agents found"
          description="Agents will appear here once configured"
        />
      ) : (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 2.5 }}>
          {groups.map((group) => (
            <Cluster key={group.key} label={group.label} help={group.help || undefined}>
              <Box sx={TILE_GRID}>
                {group.agents.map((agent) => (
                  <AgentTile
                    key={agent.id}
                    agent={agent}
                    onOpen={(a) => setEditingId(a.id)}
                    onContextMenu={(event, a) => menu.open(event, a)}
                  />
                ))}
              </Box>
            </Cluster>
          ))}
        </Box>
      )}

      <EntityContextMenu
        anchorPosition={menu.anchorPosition}
        onClose={menu.close}
        actions={menuActions(menu.payload)}
      />

      <AgentEditDialog
        agent={editing}
        onClose={() => setEditingId(null)}
        onSaved={(updated) => {
          replaceAgent(updated);
          notify("Agent updated");
        }}
        onToggle={handleToggle}
        onTest={openTest}
        onError={notifyError}
      />

      <ResetAgentDialog
        agent={resetTarget}
        open={Boolean(resetTarget)}
        busy={resetting}
        onConfirm={confirmReset}
        onClose={() => setResetTarget(null)}
      />

      <Dialog open={Boolean(testAgent)} onClose={() => setTestAgent(null)} maxWidth="md" fullWidth>
        <DialogTitle>Test {testAgent?.name}</DialogTitle>
        <DialogContent>
          {isOrchestrator(testAgent) && (
            <Alert severity="info" sx={{ mb: 2 }}>
              Shows the plan the orchestrator makes for this message; the steps are not run.
            </Alert>
          )}
          <TextField
            fullWidth
            label="Message"
            value={testMessage}
            onChange={(e) => setTestMessage(e.target.value)}
            sx={{ mt: 1, mb: 2 }}
            multiline
            minRows={3}
          />
          <TextField
            fullWidth
            label="Context (JSON)"
            value={testContextJson}
            onChange={(e) => setTestContextJson(e.target.value)}
            sx={{ mb: 2 }}
            multiline
            minRows={3}
          />
          {testResult && (
            <Box sx={{ mt: 1 }}>
              <CollapsibleAlert
                severity={testOk ? "success" : "error"}
                icon={testOk ? <CheckCircle /> : <ErrorIcon />}
                sx={{ mb: 2 }}
              >
                {testOk
                  ? isOrchestrator(testAgent)
                    ? "Plan made"
                    : `Execution complete (${testResult?.result?.iterations || 0} iterations)`
                  : testResult?.error || testResult?.result?.error || "Execution failed"}
              </CollapsibleAlert>
              <Box
                component="pre"
                sx={(theme) => ({
                  m: 0,
                  p: 2,
                  borderRadius: 1,
                  bgcolor: theme.palette.action.hover,
                  color: "text.primary",
                  border: `1px solid ${alpha(theme.palette.text.primary, 0.08)}`,
                  fontFamily: "monospace",
                  fontSize: 12,
                  whiteSpace: "pre-wrap",
                  maxHeight: 300,
                  overflow: "auto",
                })}
              >
                {JSON.stringify(testResult, null, 2)}
              </Box>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <ActionButton onClick={() => setTestAgent(null)}>Close</ActionButton>
          <ActionButton
            kind="primary"
            startIcon={testRunning ? <CircularProgress size={14} color="inherit" /> : <PlayArrow />}
            onClick={runTest}
            disabled={testRunning || !testMessage.trim()}
          >
            Run
          </ActionButton>
        </DialogActions>
      </Dialog>

      <CollapsibleAlertSnackbar
        open={snackbar.open}
        message={snackbar.message}
        severity={snackbar.severity}
        onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
      />
    </PageLayout>
  );
};

export default AgentsPage;
