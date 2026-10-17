// frontend/src/components/settings/InterconnectorEnrolments.jsx
// Master: machines that asked to join the fleet. Each one shows the
// fingerprint of its key; compare it with the one on that machine's own
// Interconnector page before approving. Above them, the transition window
// for machines that joined the old way, with no key of their own
// (backend/services/interconnector_transition.py).

import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  IconButton,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { Refresh as RefreshIcon } from "@mui/icons-material";
import * as interconnectorApi from "../../api/interconnectorService";
import { useSnackbar } from "../common/SnackbarProvider";

const STATUS_LABEL = {
  pending: "Waiting for approval",
  approved: "Joined",
  revoked: "Revoked",
};

const STATUS_COLOR = {
  pending: "warning",
  approved: "success",
  revoked: "error",
};

const errorText = (err) =>
  err?.data?.error?.message || err?.data?.message || err?.message || "Unknown error";

const machineNames = (machines) =>
  machines.map((m) => m.node_name || m.node_id).join(", ");

/** The transition window, while it is open or a machine still needs it. */
const TransitionNotice = ({ transition, busy, onChange }) => {
  const machines = transition?.legacy_machines || [];
  if (!transition || (!transition.open && machines.length === 0)) return null;
  const until = transition.until ? new Date(transition.until).toLocaleString() : "";
  const hours = transition.window_hours || 48;
  if (transition.open) {
    return (
      <Alert
        severity="warning"
        sx={{ mb: 2 }}
        action={
          <Button color="inherit" size="small" disabled={busy} onClick={() => onChange(false)}>
            Close now
          </Button>
        }
      >
        Until {until}, machines that joined the fleet the old way, with no key of their own, may
        still sync here, so they can fetch this update.
        {machines.length > 0
          ? ` Still on the old rule: ${machineNames(machines)}. Each stops once it restarts on the update and is approved below.`
          : " Every machine has enrolled; you can close it."}
      </Alert>
    );
  }
  return (
    <Alert
      severity="info"
      sx={{ mb: 2 }}
      action={
        <Button color="inherit" size="small" disabled={busy} onClick={() => onChange(true, hours)}>
          Allow for {hours} hours
        </Button>
      }
    >
      {machineNames(machines)} joined the fleet the old way, with no key of their own, and can no
      longer sync here. To update them through the Interconnector, allow them for a while.
    </Alert>
  );
};

const InterconnectorEnrolments = () => {
  const { showMessage } = useSnackbar();
  const [enrolments, setEnrolments] = useState([]);
  const [masterFingerprint, setMasterFingerprint] = useState(null);
  const [transition, setTransition] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = useCallback(async () => {
    const result = await interconnectorApi.getEnrolments();
    if (result.error) {
      setLoadError(result.error);
      return;
    }
    setLoadError(null);
    setEnrolments(result.data?.enrolments || []);
    setMasterFingerprint(result.data?.master_fingerprint || null);
    setTransition(result.data?.transition || null);
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, [load]);

  const decide = async (row, action) => {
    const name = row.node_name || row.node_id;
    if (action === "revoke" && !window.confirm(`Revoke ${name}? It stops syncing at once.`)) return;
    if (action === "remove" && !window.confirm(`Remove ${name} from the fleet?`)) return;
    setBusy(`${row.node_id}:${action}`);
    try {
      if (action === "approve") await interconnectorApi.approveEnrolment(row.node_id);
      if (action === "revoke") await interconnectorApi.revokeEnrolment(row.node_id);
      if (action === "remove") await interconnectorApi.removeEnrolment(row.node_id);
      const done = { approve: "approved; it joins at its next heartbeat", revoke: "revoked", remove: "removed" };
      showMessage(`${name} ${done[action]}`, "success");
      await load();
    } catch (err) {
      showMessage(`Could not ${action} ${name}: ${errorText(err)}`, "error");
    } finally {
      setBusy(null);
    }
  };

  const changeTransition = async (open, hours) => {
    setBusy("transition");
    try {
      const result = await interconnectorApi.setTransitionWindow(open, hours);
      setTransition(result.data?.transition || null);
      showMessage(open ? `Old-rule sync allowed for ${hours} hours` : "Old-rule sync closed", "success");
    } catch (err) {
      showMessage(`Could not change the transition window: ${errorText(err)}`, "error");
    } finally {
      setBusy(null);
    }
  };

  const waiting = enrolments.filter((row) => row.status === "pending").length;

  return (
    <Paper elevation={2} sx={{ p: 3, mb: 3 }}>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
        <Typography variant="h6">
          Machines joining the fleet{waiting ? ` (${waiting} waiting)` : ""}
        </Typography>
        <IconButton onClick={load} size="small" aria-label="Refresh">
          <RefreshIcon />
        </IconButton>
      </Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Approve a machine only when the key shown here matches the one on that
        machine&apos;s Interconnector page.
        {masterFingerprint ? ` This master's key: ${masterFingerprint}.` : ""}
      </Typography>

      <TransitionNotice transition={transition} busy={Boolean(busy)} onChange={changeTransition} />

      {loadError ? (
        <Alert severity="error">Could not load the machines: {loadError}</Alert>
      ) : enrolments.length === 0 ? (
        <Alert severity="info">No machine has asked to join yet.</Alert>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>State</TableCell>
                <TableCell>Machine</TableCell>
                <TableCell>Key</TableCell>
                <TableCell>Asked</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {enrolments.map((row) => (
                <TableRow key={row.node_id}>
                  <TableCell>
                    <Chip
                      size="small"
                      label={STATUS_LABEL[row.status] || row.status}
                      color={STATUS_COLOR[row.status] || "default"}
                    />
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">{row.node_name || row.node_id}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {[row.hostname, row.host].filter(Boolean).join(" · ")}
                    </Typography>
                  </TableCell>
                  <TableCell sx={{ fontFamily: "monospace" }}>{row.fingerprint}</TableCell>
                  <TableCell>
                    {row.requested_at ? new Date(row.requested_at).toLocaleString() : ""}
                  </TableCell>
                  <TableCell align="right">
                    <Box display="flex" gap={1} justifyContent="flex-end" flexWrap="wrap">
                      {row.status !== "approved" && (
                        <Button
                          size="small"
                          variant="contained"
                          disabled={Boolean(busy)}
                          onClick={() => decide(row, "approve")}
                        >
                          Approve
                        </Button>
                      )}
                      {row.status === "approved" && (
                        <Button
                          size="small"
                          color="error"
                          disabled={Boolean(busy)}
                          onClick={() => decide(row, "revoke")}
                        >
                          Revoke
                        </Button>
                      )}
                      <Button
                        size="small"
                        color="error"
                        disabled={Boolean(busy)}
                        onClick={() => decide(row, "remove")}
                      >
                        {row.status === "pending" ? "Decline" : "Remove"}
                      </Button>
                    </Box>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Paper>
  );
};

export default InterconnectorEnrolments;
