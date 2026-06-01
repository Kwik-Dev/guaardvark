// frontend/src/components/modals/IndexingDialog.jsx
// Per-website Google Search Console / Indexing API control panel.
// Shows queue + quota status, lets the user submit now (sitemap sync + submit),
// and toggle the auto-drip schedule.
import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  Stack,
  Switch,
  Tooltip,
  Typography,
} from "@mui/material";
import CloudUploadIcon from "@mui/icons-material/CloudUpload";
import {
  getIndexingStatus,
  submitToIndex,
  updateIndexingConfig,
} from "../../api/searchConsoleService";

const IndexingDialog = ({ open, onClose, website, onFeedback }) => {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const websiteId = website?.id;

  const loadStatus = useCallback(async () => {
    if (!websiteId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await getIndexingStatus(websiteId);
      setStatus(data);
    } catch (err) {
      setError(err.message || "Failed to load indexing status.");
    } finally {
      setLoading(false);
    }
  }, [websiteId]);

  useEffect(() => {
    if (open) loadStatus();
  }, [open, loadStatus]);

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
      // Reflect the just-synced queue counts.
      if (res?.status) setStatus(res.status);
      else loadStatus();
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

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>
        Google Indexing
        {website?.url ? (
          <Typography variant="body2" color="text.secondary" noWrap>
            {website.url}
          </Typography>
        ) : null}
      </DialogTitle>
      <DialogContent dividers>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress />
          </Box>
        ) : error ? (
          <Alert severity="error">{error}</Alert>
        ) : status ? (
          <Stack spacing={2}>
            {!status.credentials_ok && (
              <Alert severity="warning">
                Service-account credentials not found on the server. Submissions
                will fail until the Google Indexing key is configured.
              </Alert>
            )}

            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Today's quota
              </Typography>
              <Typography variant="h5">
                {status.submitted_today}
                <Typography component="span" variant="body2" color="text.secondary">
                  {" "}
                  / {status.daily_cap} submitted &nbsp;·&nbsp;{" "}
                  {status.quota_remaining} remaining
                </Typography>
              </Typography>
            </Box>

            <Divider />

            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Queue
              </Typography>
              <Stack direction="row" spacing={1}>
                <Chip label={`${queue.pending || 0} pending`} color="default" size="small" />
                <Chip label={`${queue.success || 0} indexed`} color="success" size="small" />
                <Chip label={`${queue.failed || 0} failed`} color="error" size="small" />
              </Stack>
              {status.last_sitemap_sync && (
                <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
                  Last sitemap sync:{" "}
                  {new Date(status.last_sitemap_sync).toLocaleString()}
                </Typography>
              )}
            </Box>

            <Divider />

            <Tooltip title="When on, a background job submits up to the daily quota for this site every day, newest pages first, until the sitemap is fully covered.">
              <FormControlLabel
                control={
                  <Switch
                    checked={!!status.enabled}
                    onChange={handleToggleDrip}
                  />
                }
                label="Auto-submit daily (drip up to quota)"
              />
            </Tooltip>
          </Stack>
        ) : (
          <Typography color="text.secondary">No status available.</Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        <Button
          variant="contained"
          startIcon={
            submitting ? <CircularProgress size={16} color="inherit" /> : <CloudUploadIcon />
          }
          onClick={handleSubmitNow}
          disabled={submitting || loading || (status && !status.credentials_ok)}
        >
          {submitting ? "Queuing…" : "Sync sitemap & submit now"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default IndexingDialog;
