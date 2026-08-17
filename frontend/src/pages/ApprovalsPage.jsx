// frontend/src/pages/ApprovalsPage.jsx
//
// Publishes waiting on a human. Anything an agent queues — from chat, MCP or a
// schedule — is held here until it is approved, and so is everything else when
// supervised mode is on.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  IconButton,
  Link,
  List,
  ListItemButton,
  ListItemText,
  Paper,
  Stack,
  Switch,
  Tab,
  Tabs,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  Cancel as CancelIcon,
  CheckCircle as CheckCircleIcon,
  DoNotDisturb as DoNotDisturbIcon,
  Refresh as RefreshIcon,
} from "@mui/icons-material";
import PageLayout from "../components/layout/PageLayout";
import RejectPublishDialog from "../components/connections/RejectPublishDialog";
import { useSnackbar } from "../components/common/SnackbarProvider";
import {
  approvePublish,
  cancelPublish,
  fetchConnections,
  fetchPublishes,
  rejectPublish,
} from "../api/connectionsService";
import {
  desktopNotificationsAvailable,
  desktopNotificationsGranted,
  requestDesktopNotifications,
  usePendingApprovals,
} from "../hooks/usePendingApprovals";

// status → chip colour. Mirrors ConnectionsPage so a publish reads the same
// wherever it appears.
const STATUS_COLOR = {
  awaiting_approval: "warning",
  queued: "info",
  processing: "info",
  posted: "success",
  rejected: "error",
  cancelled: "default",
  failed: "error",
};

const HISTORY_STATUSES = ["posted", "rejected", "cancelled", "failed"];

// Sources the backend always supervises, whatever the global setting says.
const AGENT_SOURCES = new Set(["chat", "mcp", "schedule"]);

const formatWhen = (iso) => {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleDateString();
};

const ApprovalsPage = () => {
  const { showMessage } = useSnackbar();
  const [tab, setTab] = useState("pending");
  const [connections, setConnections] = useState([]);
  const [queued, setQueued] = useState([]);
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [rejecting, setRejecting] = useState(null);
  const [notifyEnabled, setNotifyEnabled] = useState(desktopNotificationsGranted);

  const { pending, loading, error, refresh } = usePendingApprovals({
    notify: notifyEnabled,
  });

  // to_dict() carries connection_id but no label, so the names come from the
  // connections list and fall back to the platform slug.
  const connectionName = useCallback(
    (record) => {
      const match = connections.find((c) => c.id === record.connection_id);
      return match?.display_name || match?.handle || record.platform;
    },
    [connections],
  );

  const loadSupporting = useCallback(async () => {
    try {
      const [conns, queuedRows] = await Promise.all([
        fetchConnections(),
        fetchPublishes({ status: "queued", limit: 50 }),
      ]);
      setConnections(conns);
      setQueued(queuedRows);
    } catch {
      // Non-fatal: the queue still renders with platform slugs instead of names.
    }
  }, []);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const rows = await Promise.all(
        HISTORY_STATUSES.map((status) => fetchPublishes({ status, limit: 50 })),
      );
      setHistory(
        rows
          .flat()
          .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || "")),
      );
    } catch (err) {
      showMessage(err?.message || "Could not load history", "error");
    } finally {
      setHistoryLoading(false);
    }
  }, [showMessage]);

  useEffect(() => {
    loadSupporting();
  }, [loadSupporting]);

  useEffect(() => {
    if (tab === "history") loadHistory();
  }, [tab, loadHistory]);

  const rows = tab === "pending" ? pending : history;
  const selected = useMemo(
    () => rows.find((r) => r.id === selectedId) || null,
    [rows, selectedId],
  );

  const runAction = async (record, action, verb) => {
    setBusyId(record.id);
    try {
      await action();
      showMessage(`Publish ${verb}`);
      await Promise.all([refresh(), loadSupporting()]);
      if (tab === "history") await loadHistory();
      setSelectedId(null);
    } catch (err) {
      showMessage(err?.message || `Could not ${verb.replace(/ed$/, "")}`, "error");
    } finally {
      setBusyId(null);
      setRejecting(null);
    }
  };

  const handleToggleNotify = async (checked) => {
    if (!checked) {
      setNotifyEnabled(false);
      return;
    }
    const granted = await requestDesktopNotifications();
    setNotifyEnabled(granted);
    if (!granted) {
      showMessage(
        "The browser blocked notifications. The sidebar badge still works.",
        "warning",
      );
    }
  };

  const renderList = () => {
    if (tab === "pending" && loading) {
      return (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress />
        </Box>
      );
    }
    if (tab === "history" && historyLoading) {
      return (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress />
        </Box>
      );
    }
    if (!rows.length) {
      return (
        <Alert severity="info" sx={{ m: 2 }}>
          {tab === "pending"
            ? "Nothing is waiting for approval."
            : "No publishes have been decided yet."}
        </Alert>
      );
    }
    return (
      <List dense disablePadding sx={{ maxHeight: 520, overflowY: "auto" }}>
        {rows.map((record) => (
          <ListItemButton
            key={record.id}
            selected={selectedId === record.id}
            onClick={() => setSelectedId(record.id)}
          >
            <ListItemText
              primary={
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
                    {connectionName(record)}
                  </Typography>
                  {AGENT_SOURCES.has(record.requested_by) && (
                    <Chip label={record.requested_by} size="small" variant="outlined" />
                  )}
                </Stack>
              }
              secondary={
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                  }}
                >
                  {record.title || record.body || "(no text)"} · {formatWhen(record.created_at)}
                </Typography>
              }
            />
            {tab === "history" && (
              <Chip
                label={record.status}
                size="small"
                color={STATUS_COLOR[record.status] || "default"}
              />
            )}
          </ListItemButton>
        ))}
      </List>
    );
  };

  const renderDetail = () => {
    if (!selected) {
      return (
        <Typography variant="body2" color="text.secondary" sx={{ p: 3 }}>
          Select a publish to review it.
        </Typography>
      );
    }
    const isPending = selected.status === "awaiting_approval";
    const busy = busyId === selected.id;
    return (
      <Box sx={{ p: 3 }}>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
          <Typography variant="h6">{connectionName(selected)}</Typography>
          <Chip
            label={selected.status}
            size="small"
            color={STATUS_COLOR[selected.status] || "default"}
          />
          <Chip label={selected.visibility} size="small" variant="outlined" />
        </Stack>

        {selected.title && (
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            {selected.title}
          </Typography>
        )}
        {selected.body && (
          <Paper variant="outlined" sx={{ p: 2, mb: 2, whiteSpace: "pre-wrap" }}>
            <Typography variant="body2">{selected.body}</Typography>
          </Paper>
        )}
        {selected.link_url && (
          <Typography variant="body2" sx={{ mb: 2 }}>
            <Link href={selected.link_url} target="_blank" rel="noopener noreferrer">
              {selected.link_url}
            </Link>
          </Typography>
        )}

        <Stack direction="row" spacing={2} sx={{ mb: 2 }} flexWrap="wrap">
          <Typography variant="caption" color="text.secondary">
            Requested by {selected.requested_by} · {formatWhen(selected.created_at)}
          </Typography>
          {!!selected.media_refs?.length && (
            <Typography variant="caption" color="text.secondary">
              {selected.media_refs.length} media item(s)
            </Typography>
          )}
        </Stack>

        {selected.error_message && (
          <Alert
            severity={selected.status === "rejected" ? "info" : "error"}
            sx={{ mb: 2 }}
          >
            {selected.status === "rejected" ? "Reason: " : ""}
            {selected.error_message}
          </Alert>
        )}
        {selected.remote_url && (
          <Alert severity="success" sx={{ mb: 2 }}>
            <Link href={selected.remote_url} target="_blank" rel="noopener noreferrer">
              View the published post
            </Link>
          </Alert>
        )}

        {isPending && (
          <>
            <Divider sx={{ my: 2 }} />
            <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: "block" }}>
              Approving sends this to {connectionName(selected)} on the next pass.
            </Typography>
            <Stack direction="row" spacing={1}>
              <Button
                variant="contained"
                startIcon={<CheckCircleIcon />}
                disabled={busy}
                onClick={() =>
                  runAction(selected, () => approvePublish(selected.id), "approved")
                }
              >
                {busy ? "Working…" : "Approve"}
              </Button>
              <Button
                color="error"
                startIcon={<CancelIcon />}
                disabled={busy}
                onClick={() => setRejecting(selected)}
              >
                Reject
              </Button>
              <Button
                startIcon={<DoNotDisturbIcon />}
                disabled={busy}
                onClick={() =>
                  runAction(selected, () => cancelPublish(selected.id), "cancelled")
                }
              >
                Cancel
              </Button>
            </Stack>
          </>
        )}
      </Box>
    );
  };

  return (
    <PageLayout
      title="Approvals"
      subtitle="Publishes waiting on you before they go out"
      actions={
        <Stack direction="row" spacing={1} alignItems="center">
          {desktopNotificationsAvailable() && (
            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={notifyEnabled}
                  onChange={(e) => handleToggleNotify(e.target.checked)}
                />
              }
              label={<Typography variant="body2">Notify me</Typography>}
            />
          )}
          <Tooltip title="Refresh">
            <IconButton onClick={() => { refresh(); loadSupporting(); }}>
              <RefreshIcon />
            </IconButton>
          </Tooltip>
        </Stack>
      }
    >
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {!!queued.length && (
        <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Approved — going out next ({queued.length})
          </Typography>
          <List dense disablePadding sx={{ maxHeight: 140, overflowY: "auto" }}>
            {queued.map((record) => (
              <ListItemButton key={record.id} disabled>
                <ListItemText
                  primary={connectionName(record)}
                  secondary={record.title || record.body || "(no text)"}
                  primaryTypographyProps={{ variant: "body2" }}
                  secondaryTypographyProps={{ variant: "caption", noWrap: true }}
                />
              </ListItemButton>
            ))}
          </List>
        </Paper>
      )}

      <Paper variant="outlined">
        <Tabs
          value={tab}
          onChange={(_, value) => {
            setTab(value);
            setSelectedId(null);
          }}
          sx={{ borderBottom: 1, borderColor: "divider" }}
        >
          <Tab
            value="pending"
            label={pending.length ? `Pending (${pending.length})` : "Pending"}
          />
          <Tab value="history" label="History" />
        </Tabs>
        <Box sx={{ display: "flex", minHeight: 360 }}>
          <Box sx={{ width: 340, borderRight: 1, borderColor: "divider" }}>
            {renderList()}
          </Box>
          <Box sx={{ flex: 1, overflowY: "auto" }}>{renderDetail()}</Box>
        </Box>
      </Paper>

      <RejectPublishDialog
        open={!!rejecting}
        record={rejecting}
        busy={busyId === rejecting?.id}
        onCancel={() => setRejecting(null)}
        onConfirm={(reason) =>
          runAction(rejecting, () => rejectPublish(rejecting.id, reason), "rejected")
        }
      />
    </PageLayout>
  );
};

export default ApprovalsPage;
