// frontend/src/components/website/IndexingPanel.jsx
// Full per-website Google Indexing panel: quota + queue status, submit/sync controls,
// auto-drip toggle, and the per-URL submission log (the data IndexingDialog never showed).
// Used by the Indexing tab of WebsiteDetailPage. Reads only existing endpoints — no backend
// change required.
import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  FormControl,
  FormControlLabel,
  IconButton,
  MenuItem,
  Paper,
  Select,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import CloudUploadIcon from "@mui/icons-material/CloudUpload";
import RefreshIcon from "@mui/icons-material/Refresh";
import {
  getIndexingStatus,
  getSubmissions,
  submitToIndex,
  updateIndexingConfig,
} from "../../api/searchConsoleService";

const STATUS_CHIP = {
  success: { label: "indexed", color: "success" },
  pending: { label: "pending", color: "default" },
  failed: { label: "failed", color: "error" },
};

const formatTime = (iso) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};

const IndexingPanel = ({ website, onFeedback }) => {
  const websiteId = website?.id;

  const [status, setStatus] = useState(null);
  const [submissions, setSubmissions] = useState([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [logLoading, setLogLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const loadStatus = useCallback(async () => {
    if (!websiteId) return;
    setLoading(true);
    setError(null);
    try {
      setStatus(await getIndexingStatus(websiteId));
    } catch (err) {
      setError(err.message || "Failed to load indexing status.");
    } finally {
      setLoading(false);
    }
  }, [websiteId]);

  const loadSubmissions = useCallback(async () => {
    if (!websiteId) return;
    setLogLoading(true);
    try {
      const params = { limit: 200 };
      if (statusFilter) params.status = statusFilter;
      setSubmissions(await getSubmissions(websiteId, params));
    } catch (err) {
      // The log is supplementary — surface as feedback, not a blocking error.
      onFeedback?.({
        open: true,
        message: `Could not load submission log: ${err.message || "Unknown error"}`,
        severity: "warning",
      });
    } finally {
      setLogLoading(false);
    }
  }, [websiteId, statusFilter, onFeedback]);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    loadSubmissions();
  }, [loadSubmissions]);

  const handleSubmitNow = async () => {
    if (!websiteId) return;
    setSubmitting(true);
    try {
      const res = await submitToIndex(websiteId, { sync: true });
      onFeedback?.({
        open: true,
        message:
          res?.message ||
          "Submission job queued; URLs will be submitted in the background.",
        severity: "success",
      });
      if (res?.status) setStatus(res.status);
      else loadStatus();
      loadSubmissions();
    } catch (err) {
      onFeedback?.({
        open: true,
        message: `Submit failed: ${err.message || "Unknown error"}`,
        severity: "error",
      });
    } finally {
      setSubmitting(false);
    }
  };

  const handleToggleDrip = async (e) => {
    const enabled = e.target.checked;
    try {
      await updateIndexingConfig(websiteId, { enabled });
      setStatus((prev) => (prev ? { ...prev, enabled } : prev));
      onFeedback?.({
        open: true,
        message: enabled
          ? "Auto-submit enabled — this site will drip up to its daily quota."
          : "Auto-submit disabled.",
        severity: "info",
      });
    } catch (err) {
      onFeedback?.({
        open: true,
        message: `Could not update setting: ${err.message || "Unknown error"}`,
        severity: "error",
      });
    }
  };

  const queue = status?.queue || {};

  if (loading && !status) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (error) {
    return <Alert severity="error">{error}</Alert>;
  }

  if (!status) {
    return <Typography color="text.secondary">No indexing status available.</Typography>;
  }

  return (
    <Stack spacing={2}>
      {!status.credentials_ok && (
        <Alert severity="warning">
          Service-account credentials not found on the server. Submissions will fail until
          the Google Indexing key is configured.
        </Alert>
      )}

      {/* Quota + queue + controls */}
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={2}
          justifyContent="space-between"
          alignItems={{ xs: "flex-start", sm: "center" }}
        >
          <Box>
            <Typography variant="subtitle2" gutterBottom>
              Today's quota
            </Typography>
            <Typography variant="h5">
              {status.submitted_today}
              <Typography component="span" variant="body2" color="text.secondary">
                {" "}
                / {status.daily_cap} submitted &nbsp;·&nbsp; {status.quota_remaining} remaining
              </Typography>
            </Typography>
          </Box>
          <Button
            variant="contained"
            startIcon={
              submitting ? <CircularProgress size={16} color="inherit" /> : <CloudUploadIcon />
            }
            onClick={handleSubmitNow}
            disabled={submitting || !status.credentials_ok}
          >
            {submitting ? "Queuing…" : "Sync sitemap & submit now"}
          </Button>
        </Stack>

        <Divider sx={{ my: 2 }} />

        <Typography variant="subtitle2" gutterBottom>
          Queue
        </Typography>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Chip label={`${queue.pending || 0} pending`} size="small" />
          <Chip label={`${queue.success || 0} indexed`} color="success" size="small" />
          <Chip label={`${queue.failed || 0} failed`} color="error" size="small" />
        </Stack>

        <Stack direction="row" spacing={3} sx={{ mt: 1.5 }} flexWrap="wrap" useFlexGap>
          <Typography variant="caption" color="text.secondary">
            Last sitemap sync: {formatTime(status.last_sitemap_sync)}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Last run: {formatTime(status.last_run_at)}
          </Typography>
        </Stack>

        <Tooltip title="When on, a background job submits up to the daily quota for this site every day, newest pages first, until the sitemap is fully covered.">
          <FormControlLabel
            sx={{ mt: 1 }}
            control={<Switch checked={!!status.enabled} onChange={handleToggleDrip} />}
            label="Auto-submit daily (drip up to quota)"
          />
        </Tooltip>
      </Paper>

      {/* Per-URL submission log */}
      <Box>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
          <Typography variant="subtitle2" sx={{ flexGrow: 1 }}>
            Submission log
          </Typography>
          <FormControl size="small" sx={{ minWidth: 130 }}>
            <Select
              value={statusFilter}
              displayEmpty
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <MenuItem value="">All statuses</MenuItem>
              <MenuItem value="pending">Pending</MenuItem>
              <MenuItem value="success">Indexed</MenuItem>
              <MenuItem value="failed">Failed</MenuItem>
            </Select>
          </FormControl>
          <Tooltip title="Refresh log">
            <IconButton size="small" onClick={loadSubmissions} disabled={logLoading}>
              <RefreshIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>

        <Paper variant="outlined" sx={{ overflow: "hidden" }}>
          <TableContainer sx={{ maxHeight: 420 }}>
            <Table stickyHeader size="small">
              <TableHead>
                <TableRow>
                  <TableCell sx={{ fontWeight: "bold" }}>URL</TableCell>
                  <TableCell sx={{ fontWeight: "bold" }}>Status</TableCell>
                  <TableCell sx={{ fontWeight: "bold" }} align="right">
                    HTTP
                  </TableCell>
                  <TableCell sx={{ fontWeight: "bold" }}>Submitted</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {submissions.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={4}>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ py: 2, textAlign: "center", fontStyle: "italic" }}
                      >
                        {logLoading
                          ? "Loading…"
                          : "No submissions yet. Sync the sitemap and submit to populate this log."}
                      </Typography>
                    </TableCell>
                  </TableRow>
                ) : (
                  submissions.map((row) => {
                    const chip = STATUS_CHIP[row.status] || {
                      label: row.status,
                      color: "default",
                    };
                    return (
                      <TableRow key={row.id} hover>
                        <TableCell
                          sx={{
                            maxWidth: 360,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          <Tooltip title={row.error ? `${row.url}\n\nError: ${row.error}` : row.url}>
                            <Typography variant="body2">{row.url}</Typography>
                          </Tooltip>
                        </TableCell>
                        <TableCell>
                          <Tooltip title={row.error || ""} disableHoverListener={!row.error}>
                            <Chip label={chip.label} color={chip.color} size="small" />
                          </Tooltip>
                        </TableCell>
                        <TableCell align="right">{row.http_status ?? "—"}</TableCell>
                        <TableCell>{formatTime(row.submitted_at || row.created_at)}</TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      </Box>
    </Stack>
  );
};

export default IndexingPanel;
