// frontend/src/components/settings/InterconnectorEnrolments.jsx
// Master: machines that asked to join the fleet. Each one shows the
// fingerprint of its key; compare it with the one on that machine's own
// Interconnector page before approving.

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

const InterconnectorEnrolments = () => {
  const { showMessage } = useSnackbar();
  const [enrolments, setEnrolments] = useState([]);
  const [masterFingerprint, setMasterFingerprint] = useState(null);
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
