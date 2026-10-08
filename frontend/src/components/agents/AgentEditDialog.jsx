// frontend/src/components/agents/AgentEditDialog.jsx
// Edits one agent: on/off, model, iteration limit and instructions, with the
// agent's tools listed read-only. Only fields the server lists as editable are
// offered; the on/off chip applies at once, everything else on Save.

import React, { useEffect, useMemo, useState } from "react";
import {
  Box,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  TextField,
  Typography,
} from "@mui/material";
import PlayArrow from "@mui/icons-material/PlayArrow";
import { ActionButton, Cluster, ConfirmActionDialog, Hint, Line, SettingChip } from "../settings/ui";
import { getAgent, resetAgent, updateAgent } from "../../api/agentsService";
import { getAvailableModels } from "../../api/modelService";
import { agentEdits } from "./AgentTile";

const MAX_ITERATIONS = 50;

const FIELD_LABELS = {
  system_prompt: "Instructions",
  max_iterations: "Max iterations",
  model: "Model",
};

/** What Reset to default will put back, for the confirmation. */
export function resetFacts(agent) {
  return agentEdits(agent).map((field) => ({
    label: FIELD_LABELS[field] || field,
    value: "back to the built-in default",
  }));
}

/** The confirmation for Reset to default; the on/off switch is kept. */
export const ResetAgentDialog = ({ agent, open, busy, onConfirm, onClose }) => (
  <ConfirmActionDialog
    open={open}
    title={`Reset ${agent?.name || "agent"} to default`}
    description="Your changes to these fields are replaced by the built-in values."
    facts={agent ? resetFacts(agent) : []}
    keeps="whether the agent is on or off"
    confirmLabel="Reset"
    busy={busy}
    onConfirm={onConfirm}
    onClose={onClose}
  />
);

const formFrom = (agent) => ({
  model: agent?.model || "",
  max_iterations: String(agent?.max_iterations ?? ""),
  system_prompt: agent?.system_prompt || "",
});

/**
 * @param {object|null} agent     the list entry being edited; null closes the dialog
 * @param {function}    onClose
 * @param {function}    onSaved   called with the server's agent and { reset } after Save or Reset
 * @param {function}    onToggle  called with the agent when the Enabled chip is clicked
 * @param {function}    onTest    called with the agent to open the Test dialog
 * @param {function}    onError   called with a message when a request fails
 */
const AgentEditDialog = ({ agent, onClose, onSaved, onToggle, onTest, onError }) => {
  const open = Boolean(agent);
  const [detail, setDetail] = useState(null);
  const [form, setForm] = useState(formFrom(null));
  const [models, setModels] = useState([]);
  const [saving, setSaving] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [resetting, setResetting] = useState(false);

  const agentId = agent?.id;
  useEffect(() => {
    if (!agentId) return undefined;
    let live = true;
    setDetail(null);
    getAgent(agentId)
      .then((res) => {
        if (!live) return;
        if (res?.success) {
          setDetail(res.agent);
          setForm(formFrom(res.agent));
        } else {
          onError?.(res?.error || "Could not load the agent");
        }
      })
      .catch((err) => live && onError?.(err?.message || "Could not load the agent"));
    getAvailableModels().then((list) => live && setModels(Array.isArray(list) ? list : []));
    return () => {
      live = false;
    };
  }, [agentId]);

  const shown = detail || agent;
  const editable = useMemo(() => new Set(shown?.editable || []), [shown]);
  const base = formFrom(detail);

  const iterations = Number(form.max_iterations);
  const iterationsError =
    editable.has("max_iterations") &&
    (!Number.isInteger(iterations) || iterations < 1 || iterations > MAX_ITERATIONS);
  const promptError = editable.has("system_prompt") && !form.system_prompt.trim();

  const changes = {};
  if (detail) {
    if (editable.has("model") && form.model !== base.model) changes.model = form.model || null;
    if (editable.has("max_iterations") && form.max_iterations !== base.max_iterations) {
      changes.max_iterations = iterations;
    }
    if (editable.has("system_prompt") && form.system_prompt !== base.system_prompt) {
      changes.system_prompt = form.system_prompt;
    }
  }
  const dirty = Object.keys(changes).length > 0;

  const modelOptions = useMemo(() => {
    const names = models.map((m) => m?.name).filter(Boolean);
    const current = detail?.model;
    return current && !names.includes(current) ? [...names, current] : names;
  }, [models, detail]);

  const save = async () => {
    if (!dirty) {
      onClose();
      return;
    }
    setSaving(true);
    try {
      const res = await updateAgent(agentId, changes);
      if (res?.success) {
        onSaved?.(res.agent, { reset: false });
        onClose();
      } else {
        onError?.(res?.error || "Save failed");
      }
    } catch (err) {
      onError?.(err?.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const reset = async () => {
    setResetting(true);
    try {
      const res = await resetAgent(agentId);
      if (res?.success) {
        setDetail(res.agent);
        setForm(formFrom(res.agent));
        onSaved?.(res.agent, { reset: true });
        setConfirmReset(false);
      } else {
        onError?.(res?.error || "Reset failed");
      }
    } catch (err) {
      onError?.(err?.message || "Reset failed");
    } finally {
      setResetting(false);
    }
  };

  return (
    <>
      <Dialog open={open} onClose={saving ? undefined : onClose} maxWidth="md" fullWidth>
        <DialogTitle sx={{ pb: 0.5 }}>
          <Box sx={{ display: "flex", alignItems: "baseline", gap: 1, flexWrap: "wrap" }}>
            <span>{shown?.name}</span>
            <Typography component="span" sx={{ fontFamily: "monospace", fontSize: "0.75rem", color: "text.secondary" }}>
              {shown?.id}
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {shown?.description}
          </Typography>
        </DialogTitle>
        <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2, pt: "12px !important" }}>
          {!detail ? (
            <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
              <CircularProgress size={22} />
            </Box>
          ) : (
            <>
              <Line>
                <SettingChip
                  label="Enabled"
                  on={Boolean(agent?.enabled)}
                  onToggle={() => onToggle?.(agent)}
                  note={agent?.enabled ? undefined : "off"}
                />
                {detail.unavailable_reason && <Hint sx={{ color: "warning.main" }}>{detail.unavailable_reason}</Hint>}
              </Line>

              {(editable.has("model") || editable.has("max_iterations")) && (
                <Line>
                  {editable.has("model") && (
                    <TextField
                      select
                      size="small"
                      label="Model"
                      value={form.model}
                      SelectProps={{ displayEmpty: true }}
                      InputLabelProps={{ shrink: true }}
                      onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
                      sx={{ minWidth: 260 }}
                    >
                      <MenuItem value="">Active chat model (default)</MenuItem>
                      {modelOptions.map((name) => (
                        <MenuItem key={name} value={name}>
                          {name}
                          {models.some((m) => m?.name === name) ? "" : " (not installed)"}
                        </MenuItem>
                      ))}
                    </TextField>
                  )}
                  {editable.has("max_iterations") && (
                    <TextField
                      size="small"
                      type="number"
                      label="Max iterations"
                      value={form.max_iterations}
                      onChange={(e) => setForm((f) => ({ ...f, max_iterations: e.target.value }))}
                      error={iterationsError}
                      helperText={iterationsError ? `1 to ${MAX_ITERATIONS}` : undefined}
                      inputProps={{ min: 1, max: MAX_ITERATIONS, step: 1 }}
                      sx={{ width: 150 }}
                    />
                  )}
                </Line>
              )}

              <Cluster label="Instructions" help="Sent to the model as this agent's part of the system prompt.">
                {editable.has("system_prompt") ? (
                  <TextField
                    fullWidth
                    multiline
                    minRows={8}
                    maxRows={20}
                    value={form.system_prompt}
                    onChange={(e) => setForm((f) => ({ ...f, system_prompt: e.target.value }))}
                    error={promptError}
                    helperText={promptError ? "Instructions cannot be empty; use Reset to default instead" : undefined}
                    inputProps={{ "aria-label": "Instructions" }}
                  />
                ) : (
                  <>
                    {detail.system_prompt && (
                      <TextField
                        fullWidth
                        multiline
                        minRows={4}
                        value={detail.system_prompt}
                        InputProps={{ readOnly: true }}
                        inputProps={{ "aria-label": "Instructions" }}
                      />
                    )}
                    <Hint>The planner writes its own prompt for each request, so there is nothing to edit here.</Hint>
                  </>
                )}
              </Cluster>

              <Cluster label={`Tools (${(detail.tools_detail || []).length})`}>
                <Box component="ul" sx={{ m: 0, pl: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 0.5 }}>
                  {(detail.tools_detail || []).map((tool) => (
                    <Box component="li" key={tool.name} sx={{ fontSize: "0.8rem", lineHeight: 1.45 }}>
                      <Box component="span" sx={{ fontFamily: "monospace", color: "text.primary" }}>
                        {tool.name}
                      </Box>
                      {!tool.installed && (
                        <Box component="span" sx={{ color: "warning.main" }}>
                          {" "}
                          · not installed
                        </Box>
                      )}
                      {tool.installed && tool.requires_approval && (
                        <Box component="span" sx={{ color: "warning.main" }}>
                          {" "}
                          · needs approval (refused here)
                        </Box>
                      )}
                      {tool.description && (
                        <Box component="span" sx={{ color: "text.secondary" }}>
                          {" "}
                          — {tool.description}
                        </Box>
                      )}
                    </Box>
                  ))}
                </Box>
              </Cluster>
            </>
          )}
        </DialogContent>
        <DialogActions sx={{ justifyContent: "space-between", px: 3, pb: 2 }}>
          <ActionButton
            kind="destructive"
            disabled={!detail || agentEdits(detail).length === 0}
            tooltip={detail && agentEdits(detail).length === 0 ? "Nothing changed from the built-in default" : ""}
            onClick={() => setConfirmReset(true)}
          >
            Reset to default
          </ActionButton>
          <Box sx={{ display: "flex", gap: 1 }}>
            <ActionButton startIcon={<PlayArrow />} disabled={!detail} onClick={() => onTest?.(agent)}>
              Test
            </ActionButton>
            <ActionButton onClick={onClose} disabled={saving}>
              Cancel
            </ActionButton>
            <ActionButton
              kind={dirty ? "primary" : "neutral"}
              loading={saving}
              disabled={!detail || iterationsError || promptError}
              onClick={save}
            >
              Save
            </ActionButton>
          </Box>
        </DialogActions>
      </Dialog>
      <ResetAgentDialog
        agent={detail}
        open={confirmReset}
        busy={resetting}
        onConfirm={reset}
        onClose={() => setConfirmReset(false)}
      />
    </>
  );
};

export default AgentEditDialog;
