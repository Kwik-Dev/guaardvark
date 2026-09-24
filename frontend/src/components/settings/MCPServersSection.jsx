// frontend/src/components/settings/MCPServersSection.jsx
// MCP server management for the MCP Servers page: status, connect/disconnect,
// add/edit/remove local servers, per-tool policy, and the MCP audit log.
/* eslint-env browser, es2021 */

import React, { useCallback, useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  CircularProgress,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";

import {
  connectMcpServer,
  deleteMcpServer,
  disconnectMcpServer,
  getMcpAuditLog,
  getMcpServer,
  getMcpStatus,
  listMcpServers,
  reloadMcpConfig,
  saveMcpServer,
} from "../../api/mcpService";
import { ActionButton, ConfirmActionDialog, SettingChip, StatusPill } from "./ui";

const STATUS_TONE = {
  connected: "ok",
  connecting: "info",
  error: "error",
  disconnected: "neutral",
};

const POLICY_PILL = {
  allow: { label: "runs freely", tone: "ok" },
  confirm: { label: "asks first", tone: "warn" },
  deny: { label: "blocked", tone: "error" },
};

const auditPill = (a) => {
  if (a.success) return { label: "ok", tone: "ok" };
  if (a.decision === "needs_approval") return { label: "needs approval", tone: "warn" };
  if (a.decision === "deny") return { label: "blocked", tone: "error" };
  return { label: "failed", tone: "error" };
};

const splitList = (text) =>
  (text || "")
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);

// "KEY=value" lines <-> object. Existing secrets are shown as KEY=*** and sent
// back unchanged as "***", which the backend treats as "keep stored value".
const parsePairs = (text) => {
  const out = {};
  (text || "")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .forEach((line) => {
      const idx = line.indexOf("=");
      if (idx > 0) out[line.slice(0, idx).trim()] = line.slice(idx + 1);
    });
  return out;
};

const EMPTY_FORM = {
  name: "",
  command: "",
  args: "",
  env: "",
  autoConnect: false,
  description: "",
  keywords: "",
  allowTools: "",
  denyTools: "",
  confirmTools: "",
  autoApproveTools: "",
  timeout: "",
};

const ServerDialog = ({ open, initial, isEdit, onClose, onSaved }) => {
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (open) {
      setForm(initial || EMPTY_FORM);
      setError(null);
    }
  }, [open, initial]);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    const def = {
      command: form.command,
      args: form.args.split("\n").map((a) => a.trim()).filter(Boolean),
      env: parsePairs(form.env),
      autoConnect: form.autoConnect,
      description: form.description,
      keywords: splitList(form.keywords),
      allowTools: splitList(form.allowTools),
      denyTools: splitList(form.denyTools),
      confirmTools: splitList(form.confirmTools),
      autoApproveTools: splitList(form.autoApproveTools),
    };
    if (form.timeout) def.timeout = Number(form.timeout);
    try {
      await saveMcpServer(form.name.trim(), def);
      onSaved(form.name.trim());
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{isEdit ? `Edit MCP server "${form.name}"` : "Add MCP server"}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField label="Name" size="small" value={form.name} onChange={set("name")} disabled={isEdit}
            helperText="Letters, digits, - and _ (tools appear as mcp__<name>__<tool>)" />
          <TextField label="Command" size="small" value={form.command} onChange={set("command")}
            placeholder="npx" helperText="A program on this machine; the server runs locally" />
          <TextField label="Arguments (one per line)" size="small" multiline minRows={2}
            value={form.args} onChange={set("args")}
            placeholder={"-y\n@modelcontextprotocol/server-filesystem\n/path/to/shared"} />
          <TextField label="Environment (KEY=value per line)" size="small" multiline minRows={2}
            value={form.env} onChange={set("env")}
            helperText="Only these and safe basics (PATH, HOME, locale) reach the server; ${ENV_VAR} references work" />
          <TextField label="Description" size="small" value={form.description} onChange={set("description")} />
          <TextField label="Chat keywords (comma separated)" size="small" value={form.keywords}
            onChange={set("keywords")} helperText="Messages containing these offer this server's tools to the model" />
          <TextField label="Deny tools (globs)" size="small" value={form.denyTools} onChange={set("denyTools")} />
          <TextField label="Allow only (globs, empty = all)" size="small" value={form.allowTools}
            onChange={set("allowTools")} />
          <TextField label="Always ask before (globs)" size="small" value={form.confirmTools}
            onChange={set("confirmTools")} />
          <TextField label="Never ask before (globs)" size="small" value={form.autoApproveTools}
            onChange={set("autoApproveTools")}
            helperText="Tools marked destructive or with write/delete/run-style names ask by default" />
          <TextField label="Timeout (seconds)" size="small" type="number" value={form.timeout}
            onChange={set("timeout")} />
          <Box>
            <SettingChip
              label="Connect when the backend starts"
              on={form.autoConnect}
              onToggle={(next) => setForm((f) => ({ ...f, autoConnect: next }))}
            />
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={onClose}>Cancel</ActionButton>
        <ActionButton kind="primary" loading={saving} onClick={handleSave}
          disabled={!form.name.trim() || !form.command.trim()}>
          Save
        </ActionButton>
      </DialogActions>
    </Dialog>
  );
};

ServerDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  initial: PropTypes.object,
  isEdit: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  onSaved: PropTypes.func.isRequired,
};

const ServerDetail = ({ name }) => {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getMcpServer(name)
      .then((res) => setDetail(res.server))
      .catch((e) => setError(e.message));
  }, [name]);

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!detail) return <CircularProgress size={18} />;

  return (
    <Box sx={{ py: 1 }}>
      {detail.last_error && (
        <Alert severity="error" sx={{ mb: 1 }}>{detail.last_error}</Alert>
      )}
      {detail.tools?.length > 0 ? (
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Tool</TableCell>
              <TableCell>Policy</TableCell>
              <TableCell>Description</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {detail.tools.map((t) => {
              const pill = POLICY_PILL[t.policy] || POLICY_PILL.allow;
              return (
                <TableRow key={t.name}>
                  <TableCell sx={{ fontFamily: "monospace", fontSize: "0.75rem" }}>{t.name}</TableCell>
                  <TableCell>
                    <StatusPill label={pill.label} tone={pill.tone} tooltip={t.policyReason || ""} />
                  </TableCell>
                  <TableCell sx={{ fontSize: "0.75rem" }}>{t.description}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      ) : (
        <Typography variant="body2" color="text.secondary">
          {detail.connected ? "This server exposes no tools." : "Connect to see this server's tools."}
        </Typography>
      )}
      {detail.stderr_tail && (
        <Box component="pre" sx={{ mt: 1, p: 1, maxHeight: 160, overflow: "auto", fontSize: "0.7rem",
          bgcolor: "action.hover", borderRadius: 1, whiteSpace: "pre-wrap" }}>
          {detail.stderr_tail.slice(-2000)}
        </Box>
      )}
    </Box>
  );
};

ServerDetail.propTypes = { name: PropTypes.string.isRequired };

// Editable definition (from GET /servers/<name>; secret values arrive as "***").
const toForm = (name, d) => ({
  ...EMPTY_FORM,
  name,
  command: d.command || "",
  args: (d.args || []).join("\n"),
  env: Object.entries(d.env || {}).map(([k, v]) => `${k}=${v}`).join("\n"),
  autoConnect: !!d.autoConnect,
  description: d.description || "",
  keywords: (d.keywords || []).join(", "),
  allowTools: (d.allowTools || []).join(", "),
  denyTools: (d.denyTools || []).join(", "),
  confirmTools: (d.confirmTools || []).join(", "),
  autoApproveTools: (d.autoApproveTools || []).join(", "),
  timeout: d.timeout || "",
});

const MCPServersSection = () => {
  const [status, setStatus] = useState(null);
  const [servers, setServers] = useState([]);
  const [configErrors, setConfigErrors] = useState([]);
  const [audit, setAudit] = useState([]);
  const [busy, setBusy] = useState({});
  const [expanded, setExpanded] = useState(null);
  const [showAudit, setShowAudit] = useState(false);
  const [dialog, setDialog] = useState({ open: false, initial: null, isEdit: false });
  const [removing, setRemoving] = useState(null);
  const [message, setMessage] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [st, list] = await Promise.all([getMcpStatus(), listMcpServers()]);
      setStatus(st);
      setServers(list.servers || []);
      setConfigErrors(list.config_errors || []);
    } catch (e) {
      setMessage({ severity: "error", text: `Could not load MCP status: ${e.message}` });
    }
  }, []);

  const refreshAudit = useCallback(async () => {
    try {
      const res = await getMcpAuditLog(25);
      setAudit(res.entries || []);
    } catch (e) {
      setAudit([]);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    if (showAudit) refreshAudit();
  }, [showAudit, refreshAudit]);

  const withBusy = async (name, fn, okText) => {
    setBusy((b) => ({ ...b, [name]: true }));
    setMessage(null);
    try {
      await fn();
      if (okText) setMessage({ severity: "success", text: okText });
    } catch (e) {
      setMessage({ severity: "error", text: e.message });
    } finally {
      setBusy((b) => ({ ...b, [name]: false }));
      refresh();
    }
  };

  const openEdit = async (name) => {
    try {
      const res = await getMcpServer(name);
      setDialog({ open: true, initial: toForm(name, res.server.definition || {}), isEdit: true });
    } catch (e) {
      setMessage({ severity: "error", text: e.message });
    }
  };

  const confirmRemove = async () => {
    const name = removing?.name;
    setRemoving(null);
    if (name) await withBusy(name, () => deleteMcpServer(name), `Removed ${name}`);
  };

  if (status && !status.mcp_enabled) {
    return <Alert severity="info">MCP is disabled. Set GUAARDVARK_MCP_ENABLED=true and restart.</Alert>;
  }

  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
        <Box sx={{ flexGrow: 1, display: "flex", gap: 1, flexWrap: "wrap" }}>
          {status ? (
            <>
              <StatusPill
                tone={status.servers_connected > 0 ? "ok" : "neutral"}
                label={`${status.servers_connected}/${status.servers_configured} connected`}
              />
              <StatusPill label={`${status.total_tools_available} tool${status.total_tools_available === 1 ? "" : "s"}`} />
              <StatusPill label={`${status.total_calls} call${status.total_calls === 1 ? "" : "s"}`} />
              {!status.sdk_available && <StatusPill tone="error" label="mcp package missing" />}
            </>
          ) : (
            <Typography variant="body2" color="text.secondary">Loading…</Typography>
          )}
        </Box>
        <ActionButton onClick={refresh}>Refresh</ActionButton>
        <ActionButton loading={!!busy._reload}
          onClick={() => withBusy("_reload", reloadMcpConfig, "Configuration reloaded")}>
          Reload config
        </ActionButton>
        <ActionButton onClick={() => setDialog({ open: true, initial: null, isEdit: false })}>
          Add server
        </ActionButton>
      </Stack>

      {message && (
        <Alert severity={message.severity} onClose={() => setMessage(null)} sx={{ mb: 1 }}>{message.text}</Alert>
      )}
      {configErrors.map((err) => (
        <Alert key={err} severity="warning" sx={{ mb: 1 }}>{err}</Alert>
      ))}

      {servers.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No MCP servers configured. Add one to give the assistant new tools (files, databases, other
          local programs).
        </Typography>
      ) : (
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Server</TableCell>
              <TableCell>Status</TableCell>
              <TableCell align="right">Tools</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {servers.map((s) => (
              <React.Fragment key={s.name}>
                <TableRow hover>
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{s.name}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {s.command}
                      {s.auto_connect ? " · connects at start" : ""}
                      {s.disabled ? " · disabled" : ""}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <StatusPill label={s.status} tone={STATUS_TONE[s.status] || "neutral"}
                      tooltip={s.last_error || ""} />
                  </TableCell>
                  <TableCell align="right">{s.connected ? s.tool_count : "–"}</TableCell>
                  <TableCell align="right">
                    <Box sx={{ display: "inline-flex", gap: 0.75 }}>
                      <ActionButton onClick={() => setExpanded(expanded === s.name ? null : s.name)}>
                        {expanded === s.name ? "Hide tools" : "Tools"}
                      </ActionButton>
                      {s.connected ? (
                        <ActionButton loading={!!busy[s.name]}
                          onClick={() => withBusy(s.name, () => disconnectMcpServer(s.name))}>
                          Disconnect
                        </ActionButton>
                      ) : (
                        <ActionButton loading={!!busy[s.name]} disabled={s.disabled}
                          onClick={() => withBusy(s.name, () => connectMcpServer(s.name),
                            `Connected to ${s.name}`)}>
                          Connect
                        </ActionButton>
                      )}
                      <ActionButton onClick={() => openEdit(s.name)}>Edit</ActionButton>
                      <ActionButton kind="destructive" onClick={() => setRemoving(s)}>Remove</ActionButton>
                    </Box>
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell colSpan={4} sx={{ py: 0, borderBottom: expanded === s.name ? undefined : "none" }}>
                    <Collapse in={expanded === s.name} unmountOnExit>
                      <ServerDetail name={s.name} key={`${s.name}-${s.status}`} />
                    </Collapse>
                  </TableCell>
                </TableRow>
              </React.Fragment>
            ))}
          </TableBody>
        </Table>
      )}

      <Box sx={{ mt: 1.5 }}>
        <ActionButton onClick={() => setShowAudit((v) => !v)}>
          {showAudit ? "Hide recent MCP activity" : "Recent MCP activity"}
        </ActionButton>
        <Collapse in={showAudit} unmountOnExit>
          {audit.length === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>No MCP calls yet.</Typography>
          ) : (
            <Table size="small" sx={{ mt: 1 }}>
              <TableHead>
                <TableRow>
                  <TableCell>Time</TableCell>
                  <TableCell>Call</TableCell>
                  <TableCell>By</TableCell>
                  <TableCell>Result</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {audit.map((a, i) => {
                  const pill = auditPill(a);
                  return (
                    <TableRow key={`${a.ts}-${i}`}>
                      <TableCell sx={{ fontSize: "0.7rem", whiteSpace: "nowrap" }}>
                        {new Date(a.ts).toLocaleTimeString()}
                      </TableCell>
                      <TableCell sx={{ fontSize: "0.7rem", fontFamily: "monospace" }}>
                        {a.server}/{a.tool}
                      </TableCell>
                      <TableCell sx={{ fontSize: "0.7rem" }}>{a.caller}</TableCell>
                      <TableCell>
                        <StatusPill label={pill.label} tone={pill.tone}
                          tooltip={a.error || (a.args ? JSON.stringify(a.args) : "")} />
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </Collapse>
      </Box>

      <ServerDialog
        open={dialog.open}
        initial={dialog.initial}
        isEdit={dialog.isEdit}
        onClose={() => setDialog({ open: false, initial: null, isEdit: false })}
        onSaved={(name) => {
          setDialog({ open: false, initial: null, isEdit: false });
          setMessage({ severity: "success", text: `Saved ${name}` });
          refresh();
        }}
      />

      <ConfirmActionDialog
        open={!!removing}
        title={`Remove MCP server "${removing?.name || ""}"`}
        description="Disconnects the server and deletes its entry from the MCP configuration."
        facts={removing ? [
          { label: "Command", value: removing.command || "–" },
          { label: "Tools it offers", value: removing.connected ? removing.tool_count : "not connected" },
        ] : []}
        keeps="the server program itself and anything it wrote"
        confirmLabel="Remove"
        onConfirm={confirmRemove}
        onClose={() => setRemoving(null)}
      />
    </Box>
  );
};

export default MCPServersSection;
