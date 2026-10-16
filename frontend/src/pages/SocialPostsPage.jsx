// frontend/src/pages/SocialPostsPage.jsx
//
// Every publish to a social account, like WordPress Pages lists a site's
// pages: status, scheduled time, AI disclosure, the link on the platform, and
// Delete on platform where the provider supports it. Approving stays on the
// Approvals page; this page links there. New post opens the composer without
// an asset, for text and link posts.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import RefreshIcon from "@mui/icons-material/Refresh";
import { useNavigate, useSearchParams } from "react-router-dom";

import PageLayout from "../components/layout/PageLayout";
import PublishModal from "../components/modals/PublishModal";
import { useSnackbar } from "../components/common/SnackbarProvider";
import { ActionButton, ConfirmActionDialog, StatusPill } from "../components/settings/ui";
import {
  cancelPublish,
  deletePublishRemote,
  fetchConnections,
  fetchProviders,
  fetchPublishes,
} from "../api/connectionsService";
import { STATUS_TONE, deleteOnPlatform, statusLabel } from "../components/social/platformRules";

// Filter choices; a value may name several statuses, comma separated.
const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "awaiting_approval", label: "Waiting for approval" },
  { value: "scheduled", label: "Scheduled" },
  { value: "queued,processing", label: "Sending" },
  { value: "posted", label: "Posted" },
  { value: "failed", label: "Failed" },
  { value: "deleted", label: "Deleted on platform" },
  { value: "rejected,cancelled", label: "Rejected or cancelled" },
];

const CANCELLABLE = new Set(["awaiting_approval", "queued", "scheduled"]);

const formatWhen = (iso) => (iso ? new Date(iso).toLocaleString() : "—");

const SocialPostsPage = () => {
  const navigate = useNavigate();
  const { showMessage } = useSnackbar();
  const [searchParams, setSearchParams] = useSearchParams();
  const connectionFilter = searchParams.get("connection_id") || "";
  const statusFilter = searchParams.get("status") || "";

  const [connections, setConnections] = useState([]);
  const [specs, setSpecs] = useState({});
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [composing, setComposing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [conns, providerSpecs, rows] = await Promise.all([
        fetchConnections("social"),
        fetchProviders("social"),
        fetchPublishes({ connection_id: connectionFilter, status: statusFilter, limit: 200 }),
      ]);
      setConnections(conns);
      setSpecs(Object.fromEntries(providerSpecs.map((s) => [s.provider, s])));
      setRecords(rows);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [connectionFilter, statusFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const setFilter = (key, value) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearchParams(next, { replace: true });
  };

  const accountName = useCallback(
    (record) => {
      const match = connections.find((c) => c.id === record.connection_id);
      return match ? match.display_name || match.handle || match.account_slug : "(removed account)";
    },
    [connections],
  );

  // Scheduled posts first, soonest first; then everything else, newest first.
  const rows = useMemo(() => {
    const upcoming = records
      .filter((r) => r.status === "scheduled")
      .sort((a, b) => (a.scheduled_at || "").localeCompare(b.scheduled_at || ""));
    const rest = records.filter((r) => r.status !== "scheduled");
    return [...upcoming, ...rest];
  }, [records]);

  const handleCancel = async (record) => {
    setBusyId(record.id);
    try {
      await cancelPublish(record.id);
      showMessage("Publish cancelled.", "success");
      await load();
    } catch (e) {
      showMessage(e.message, "error");
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async () => {
    if (!deleting) return;
    setBusyId(deleting.id);
    try {
      const result = await deletePublishRemote(deleting.id);
      showMessage(result.message || "Deleted on the platform.", "success");
      setDeleting(null);
      await load();
    } catch (e) {
      showMessage(e.message, "error");
    } finally {
      setBusyId(null);
    }
  };

  const deletingSpec = deleting ? specs[deleting.platform] : null;

  return (
    <PageLayout
      title="Social Posts"
      actions={
        <Stack direction="row" spacing={1} alignItems="center">
          <ActionButton kind="link" onClick={() => navigate("/social/accounts")}>
            Social Accounts
          </ActionButton>
          <ActionButton kind="link" onClick={() => navigate("/approvals")}>
            Approvals
          </ActionButton>
          <ActionButton startIcon={<AddIcon />} onClick={() => setComposing(true)}>
            New post
          </ActionButton>
          <Tooltip title="Refresh">
            <IconButton onClick={load} size="small">
              <RefreshIcon />
            </IconButton>
          </Tooltip>
        </Stack>
      }
    >
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ mb: 2 }}>
        <FormControl size="small" sx={{ minWidth: 220 }}>
          <InputLabel>Account</InputLabel>
          <Select
            label="Account"
            value={connectionFilter}
            onChange={(e) => setFilter("connection_id", e.target.value)}
          >
            <MenuItem value="">All accounts</MenuItem>
            {connections.map((conn) => (
              <MenuItem key={conn.id} value={String(conn.id)}>
                {(specs[conn.provider]?.label || conn.provider) + " · " + (conn.display_name || conn.handle || conn.account_slug)}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 220 }}>
          <InputLabel>Status</InputLabel>
          <Select label="Status" value={statusFilter} onChange={(e) => setFilter("status", e.target.value)}>
            {STATUS_FILTERS.map((option) => (
              <MenuItem key={option.value || "all"} value={option.value}>
                {option.label}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {!loading && !rows.length ? (
        <Box sx={{ textAlign: "center", py: 6 }}>
          <Typography variant="body1" color="text.secondary" gutterBottom>
            No posts here yet.
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Write one with New post, or publish from Media or the Studio with the Publish action;
            posts show here with their status.
          </Typography>
        </Box>
      ) : (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Account</TableCell>
                <TableCell>Post</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Scheduled</TableCell>
                <TableCell>Posted</TableCell>
                <TableCell>AI label</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((record) => {
                const spec = specs[record.platform];
                const platformLabel = spec?.label || record.platform;
                const removal = deleteOnPlatform(record, spec?.capabilities);
                const disclosure = spec?.capabilities?.ai_disclosure;
                return (
                  <TableRow key={record.id} hover>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {accountName(record)}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {platformLabel}
                      </Typography>
                    </TableCell>
                    <TableCell sx={{ maxWidth: 360 }}>
                      {record.title && (
                        <Typography variant="body2" sx={{ fontWeight: 500 }} noWrap>
                          {record.title}
                        </Typography>
                      )}
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}
                      >
                        {record.body || (record.media_refs?.length ? `${record.media_refs.length} media item(s)` : "(no text)")}
                      </Typography>
                      {record.error_message && (
                        <Typography
                          variant="caption"
                          sx={{ display: "block", color: record.status === "failed" ? "error.main" : "text.secondary" }}
                        >
                          {record.error_message}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <StatusPill label={statusLabel(record, platformLabel)} tone={STATUS_TONE[record.status] || "neutral"} />
                    </TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      <Typography variant="body2">{formatWhen(record.scheduled_at)}</Typography>
                      {record.scheduled_at && (
                        <Typography variant="caption" color="text.secondary">
                          {record.scheduled_natively ? `by ${platformLabel}` : "by Guaardvark"}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>{formatWhen(record.posted_at)}</TableCell>
                    <TableCell>
                      {record.ai_disclosure ? (
                        <Tooltip
                          title={
                            disclosure?.mechanism === "platform_flag"
                              ? `Set as ${disclosure.flag}`
                              : `Line in the post: ${disclosure?.text || ""}`
                          }
                        >
                          <span>Yes</span>
                        </Tooltip>
                      ) : (
                        "No"
                      )}
                    </TableCell>
                    <TableCell align="right">
                      <Stack direction="row" spacing={0.5} justifyContent="flex-end" alignItems="center">
                        {record.remote_url && (
                          <Tooltip title={`Open on ${platformLabel}`}>
                            <IconButton
                              size="small"
                              component="a"
                              href={record.remote_url}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              <OpenInNewIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                        {record.status === "awaiting_approval" && (
                          <ActionButton kind="link" onClick={() => navigate("/approvals")}>
                            Review
                          </ActionButton>
                        )}
                        {CANCELLABLE.has(record.status) && !record.scheduled_natively && (
                          <ActionButton loading={busyId === record.id} onClick={() => handleCancel(record)}>
                            Cancel
                          </ActionButton>
                        )}
                        {removal.show && (
                          <ActionButton
                            kind="destructive"
                            disabled={!removal.ok}
                            tooltip={removal.reason}
                            onClick={() => setDeleting(record)}
                          >
                            Delete on platform
                          </ActionButton>
                        )}
                      </Stack>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <PublishModal
        open={composing}
        onClose={() => {
          setComposing(false);
          load();
        }}
        onFeedback={(feedback) => showMessage(feedback.message, feedback.severity)}
      />

      <ConfirmActionDialog
        open={Boolean(deleting)}
        title={`Delete this post on ${deletingSpec?.label || deleting?.platform || "the platform"}?`}
        description={
          deleting?.status === "scheduled"
            ? "The platform still holds this post for its time. Deleting it there means it never goes out."
            : "Removes the post from the platform. Its record stays here, marked deleted."
        }
        facts={
          deleting
            ? [
                { label: "Account", value: accountName(deleting) },
                { label: deleting.status === "scheduled" ? "Scheduled" : "Posted", value: formatWhen(deleting.scheduled_at && deleting.status === "scheduled" ? deleting.scheduled_at : deleting.posted_at) },
                { label: "Text", value: (deleting.title || deleting.body || "").slice(0, 120) || "(no text)" },
              ]
            : []
        }
        confirmLabel="Delete on platform"
        busy={busyId === deleting?.id}
        onConfirm={handleDelete}
        onClose={() => setDeleting(null)}
      />
    </PageLayout>
  );
};

export default SocialPostsPage;
