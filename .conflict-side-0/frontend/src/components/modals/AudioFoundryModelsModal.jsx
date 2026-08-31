// frontend/src/components/modals/AudioFoundryModelsModal.jsx
// Check and install Audio Foundry weights (voice, music, FX) plus the sidecar.

import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
  Typography,
  CircularProgress,
  Box,
  Chip,
  LinearProgress,
  Divider,
} from "@mui/material";
import GraphicEqIcon from "@mui/icons-material/GraphicEq";
import CloudDownloadIcon from "@mui/icons-material/CloudDownload";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import axios from "axios";
import { ActionButton, StatusPill } from "../settings/ui";
import { enablePlugin, startPlugin } from "../../api/pluginsService";

const GROUP_LABELS = {
  voice: "Voice",
  music: "Music",
  fx: "Sound FX",
};

const GROUP_ORDER = ["voice", "music", "fx"];

const sizeLabel = (gb) => {
  const n = Number(gb) || 0;
  if (n >= 1) return `${n.toFixed(n >= 10 ? 0 : 1)} GB`;
  if (n <= 0) return null;
  return `${Math.round(n * 1024)} MB`;
};

const AudioFoundryModelsModal = ({
  open,
  onClose,
  showMessage,
  highlightModelId,
  onChanged,
}) => {
  const [models, setModels] = useState([]);
  const [plugin, setPlugin] = useState({ running: false, enabled: false, status: "unknown" });
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState(null);
  const [dl, setDl] = useState({
    is_downloading: false,
    current_id: null,
    progress: 0,
    status: "idle",
    speed_mbps: 0,
    downloaded_gb: 0,
    total_gb: 0,
    error: null,
  });
  const highlightRef = useRef(null);
  const showMessageRef = useRef(showMessage);
  useEffect(() => {
    showMessageRef.current = showMessage;
  }, [showMessage]);

  const fetchModels = useCallback(async () => {
    try {
      setLoading(true);
      const res = await axios.get("/api/audio-foundry/models");
      if (res.data?.success) {
        setModels(res.data.models || []);
        setPlugin(res.data.plugin || { running: false, enabled: false, status: "unknown" });
        setError(null);
      } else {
        setError("Failed to load audio models");
      }
    } catch (err) {
      setError(err.message || "Error fetching audio models");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await axios.get("/api/audio-foundry/models/download-status");
      if (res.data?.success) {
        const next = res.data;
        setDl((prev) => {
          if (prev.is_downloading && !next.is_downloading) {
            if (next.status === "completed") {
              showMessageRef.current?.("Model installed", "success");
              fetchModels();
              onChanged?.();
            } else if (next.status === "failed") {
              showMessageRef.current?.(`Download failed: ${next.error || "unknown"}`, "error");
              fetchModels();
            }
          }
          return next;
        });
      }
    } catch {
      /* transient */
    }
  }, [fetchModels, onChanged]);

  useEffect(() => {
    if (open) {
      fetchModels();
      fetchStatus();
    } else {
      setModels([]);
      setError(null);
    }
  }, [open, fetchModels, fetchStatus]);

  useEffect(() => {
    let id;
    if (open && dl.is_downloading) {
      id = setInterval(fetchStatus, 1000);
    }
    return () => clearInterval(id);
  }, [open, dl.is_downloading, fetchStatus]);

  useEffect(() => {
    if (open && highlightModelId && !loading && highlightRef.current) {
      highlightRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [open, highlightModelId, loading, models]);

  const handleInstall = async (assetId) => {
    try {
      const res = await axios.post("/api/audio-foundry/models/download", { id: assetId });
      if (res.data?.success) {
        if (res.data.already_installed) {
          showMessageRef.current?.("Already installed", "info");
          fetchModels();
          onChanged?.();
          return;
        }
        const asset = models.find((m) => m.id === assetId);
        showMessageRef.current?.(`Started downloading ${asset?.name || assetId}…`, "info");
        setDl({
          is_downloading: true,
          current_id: assetId,
          progress: 0,
          status: "starting",
          speed_mbps: 0,
          downloaded_gb: 0,
          total_gb: asset?.size_gb || 0,
          error: null,
        });
      } else {
        showMessageRef.current?.(res.data?.error || "Failed to start", "error");
      }
    } catch (err) {
      if (err.response?.status === 409) {
        showMessageRef.current?.("Another download is already running.", "warning");
      } else {
        const msg =
          err.response?.data?.error ||
          err.message ||
          "Error starting download";
        showMessageRef.current?.(msg, "error");
      }
    }
  };

  const missing = models.filter((m) => !m.installed);
  const missingTotalGb = missing.reduce((sum, m) => sum + (m.size_gb || 0), 0);

  const handleInstallAllMissing = async () => {
    if (!missing.length) return;
    for (const m of missing) {
      await handleInstall(m.id);
      await new Promise((resolve) => {
        const check = setInterval(async () => {
          try {
            const res = await axios.get("/api/audio-foundry/models/download-status");
            if (res.data?.success && !res.data.is_downloading) {
              clearInterval(check);
              resolve();
            }
          } catch {
            /* keep waiting */
          }
        }, 700);
      });
    }
    fetchModels();
    onChanged?.();
  };

  const handleStartPlugin = async () => {
    setStarting(true);
    try {
      try {
        await enablePlugin("audio_foundry");
      } catch {
        /* already enabled is fine */
      }
      await startPlugin("audio_foundry");
      showMessageRef.current?.("Audio Foundry started", "success");
      await fetchModels();
      onChanged?.();
    } catch (err) {
      showMessageRef.current?.(
        err?.message || "Could not start Audio Foundry. Check Plugins and audio_foundry.log.",
        "error",
      );
    } finally {
      setStarting(false);
    }
  };

  const pluginRunning = !!plugin.running;
  const pluginTone = pluginRunning ? "ok" : plugin.status === "starting" ? "warn" : "error";

  const renderRow = (m) => {
    const isThis = dl.is_downloading && dl.current_id === m.id;
    const isHighlight = !!highlightModelId && m.id === highlightModelId;
    const size = sizeLabel(m.size_gb);
    return (
      <ListItem
        key={m.id}
        ref={isHighlight ? highlightRef : undefined}
        divider
        sx={{
          py: 1.5,
          alignItems: "flex-start",
          bgcolor: isHighlight ? "action.selected" : undefined,
        }}
      >
        <ListItemIcon sx={{ mt: 0.5 }}>
          <GraphicEqIcon color={m.installed ? "primary" : "action"} />
        </ListItemIcon>
        <ListItemText
          primary={
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
              <Typography variant="body1" fontWeight={500}>
                {m.name}
              </Typography>
              {size && <Chip label={size} size="small" variant="outlined" />}
              {m.gated && (
                <Chip label="Gated" size="small" color="warning" variant="outlined" />
              )}
            </Box>
          }
          secondary={
            <Box component="span" sx={{ display: "block", mt: 0.5 }}>
              <Typography variant="caption" sx={{ display: "block", color: "text.secondary" }}>
                {m.description}
              </Typography>
              {m.gated && m.terms_url && !m.installed && (
                <Typography variant="caption" sx={{ display: "block", mt: 0.25 }}>
                  Accept terms at{" "}
                  <a href={m.terms_url} target="_blank" rel="noreferrer">
                    {m.hf_repo}
                  </a>
                  , then set HF_TOKEN in .env.
                </Typography>
              )}
              {m.hf_repo && (
                <Typography
                  variant="caption"
                  sx={{ display: "block", color: "text.disabled", fontFamily: "monospace", mt: 0.25 }}
                >
                  {m.hf_repo}
                </Typography>
              )}
            </Box>
          }
        />
        <Box sx={{ ml: 2, minWidth: 160, textAlign: "right" }}>
          {isThis ? (
            <Box sx={{ width: 160 }}>
              <Typography variant="caption" noWrap>
                {dl.status === "starting"
                  ? "Starting…"
                  : `${dl.progress}% — ${dl.speed_mbps} MB/s`}
              </Typography>
              <LinearProgress
                variant={dl.progress > 0 ? "determinate" : "indeterminate"}
                value={dl.progress}
                sx={{ mt: 0.5 }}
              />
              <Typography variant="caption" color="text.secondary">
                {(dl.downloaded_gb || 0).toFixed(2)} / {(dl.total_gb || 0).toFixed(2)} GB
              </Typography>
            </Box>
          ) : m.installed ? (
            <Chip
              icon={<CheckCircleIcon />}
              label="Installed"
              color="success"
              size="small"
              variant="outlined"
            />
          ) : (
            <Button
              variant="outlined"
              size="small"
              startIcon={<CloudDownloadIcon />}
              onClick={() => handleInstall(m.id)}
              disabled={dl.is_downloading}
            >
              Install
            </Button>
          )}
        </Box>
      </ListItem>
    );
  };

  return (
    <Dialog
      open={open}
      onClose={() => !dl.is_downloading && !starting && onClose()}
      maxWidth="md"
      fullWidth
    >
      <DialogTitle>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <GraphicEqIcon />
          Audio Studio models
        </Box>
        <Typography variant="body2" sx={{ color: "text.secondary", mt: 0.5 }}>
          Voice, music and FX weights install into the shared Hugging Face cache.
          Generation will not download them on its own.
        </Typography>
      </DialogTitle>

      <DialogContent dividers>
        {error && (
          <Box mb={2}>
            <Typography color="error">{error}</Typography>
          </Box>
        )}

        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 2, flexWrap: "wrap" }}>
          <StatusPill
            tone={pluginTone}
            label={
              pluginRunning
                ? "Audio Foundry running"
                : `Audio Foundry ${plugin.status || "stopped"}`
            }
          />
          {!pluginRunning && (
            <ActionButton onClick={handleStartPlugin} loading={starting}>
              Start plugin
            </ActionButton>
          )}
        </Box>

        {loading ? (
          <Box display="flex" justifyContent="center" p={3}>
            <CircularProgress />
          </Box>
        ) : (
          GROUP_ORDER.map((group, idx) => {
            const rows = models.filter((m) => m.group === group);
            if (!rows.length) return null;
            return (
              <Box key={group}>
                {idx > 0 && <Divider sx={{ my: 1.5 }} />}
                <Typography
                  variant="subtitle2"
                  color="text.secondary"
                  sx={{ mb: 0.5, px: 1 }}
                >
                  {GROUP_LABELS[group] || group}
                </Typography>
                <List disablePadding>{rows.map(renderRow)}</List>
              </Box>
            );
          })
        )}
      </DialogContent>

      <DialogActions>
        {missingTotalGb > 0 && (
          <Button
            onClick={handleInstallAllMissing}
            disabled={dl.is_downloading || loading || starting}
            startIcon={<CloudDownloadIcon />}
          >
            Install all missing ({missingTotalGb.toFixed(1)} GB)
          </Button>
        )}
        <Box sx={{ flex: 1 }} />
        <Button onClick={onClose} disabled={dl.is_downloading || starting}>
          {dl.is_downloading ? "Downloading…" : "Close"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default AudioFoundryModelsModal;
