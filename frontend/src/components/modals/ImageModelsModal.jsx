// frontend/src/components/modals/ImageModelsModal.jsx
import React, { useState, useEffect, useCallback, useRef } from "react";
import { formatUiError } from "../../utils/uiError";
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
} from "@mui/material";
import ImageIcon from "@mui/icons-material/Image";
import CloudDownloadIcon from "@mui/icons-material/CloudDownload";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import axios from "axios";
import { ActionButton, ConfirmActionDialog, SettingChip } from "../settings/ui";
import AddImageModelDialog from "./AddImageModelDialog";

const modelMatchesDownload = (model, currentModel) =>
  !!currentModel && (currentModel === model.path || currentModel === model.id);

const ImageModelsModal = ({ open, onClose, showMessage }) => {
  const [models, setModels] = useState([]);
  const [adapters, setAdapters] = useState([]);
  // Chat photo-tool packs (Qwen-Image-Edit, Kontext, PuLID, background removal).
  const [editing, setEditing] = useState([]);
  const [addOpen, setAddOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState(null);
  const [loading, setLoading] = useState(true);
  const [downloadStatus, setDownloadStatus] = useState({
    is_downloading: false,
    current_model: null,
    progress: 0,
    status: "idle",
    speed_mbps: 0,
    downloaded_gb: 0,
    total_gb: 0,
  });
  const [error, setError] = useState(null);

  const showMessageRef = useRef(showMessage);
  useEffect(() => {
    showMessageRef.current = showMessage;
  }, [showMessage]);

  const fetchModels = useCallback(async () => {
    try {
      setLoading(true);
      const res = await axios.get("/api/batch-image/models");
      if (res.data.success) {
        setModels(res.data.data.models);
        setAdapters(res.data.data.adapters || []);
        setEditing(res.data.data.editing || []);
      } else {
        setError("Failed to load models");
      }
    } catch (err) {
      setError(err.message || "Error fetching models");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchDownloadStatus = useCallback(async () => {
    try {
      const res = await axios.get("/api/batch-image/models/download-status");
      if (res.data.success) {
        const status = res.data.data;
        setDownloadStatus(status);
        if (!status.is_downloading && status.status === "completed") {
          showMessageRef.current?.("Model download completed!", "success");
          fetchModels();
          setDownloadStatus((prev) => ({ ...prev, status: "idle", current_model: null }));
        } else if (!status.is_downloading && status.status === "failed") {
          showMessageRef.current?.(`Download failed: ${status.error}`, "error");
          fetchModels();
          setDownloadStatus((prev) => ({ ...prev, status: "idle", current_model: null }));
        }
      }
    } catch (err) {
      console.error("Failed to fetch download status", err);
    }
  }, [fetchModels]);

  useEffect(() => {
    if (open) {
      fetchModels();
      fetchDownloadStatus();
    } else {
      setModels([]);
      setError(null);
    }
  }, [open, fetchModels, fetchDownloadStatus]);

  useEffect(() => {
    let interval;
    if (open && downloadStatus.is_downloading) {
      interval = setInterval(fetchDownloadStatus, 1000);
    }
    return () => clearInterval(interval);
  }, [open, downloadStatus.is_downloading, fetchDownloadStatus]);

  const handleDownload = async (model) => {
    const modelRef = model.path || model.id;
    try {
      const res = await axios.post("/api/batch-image/models/download", { model_path: modelRef });
      if (res.data.success) {
        const startedPath = res.data.data?.model_path || modelRef;
        showMessage?.(`Started downloading ${model.name || model.id}...`, "info");
        setDownloadStatus({
          is_downloading: true,
          current_model: startedPath,
          progress: 0,
          status: "starting",
          speed_mbps: 0,
          downloaded_gb: 0,
          total_gb: model.size_gb || 0,
        });
      } else {
        showMessage?.(res.data.error || "Failed to start download", "error");
      }
    } catch (err) {
      if (err.response?.status === 409) {
        showMessage?.("A download is already in progress.", "warning");
      } else {
        showMessage?.(formatUiError(err.response?.data?.error) || err.message || "Error starting download", "error");
      }
    }
  };

  const handleRemove = async () => {
    if (!removeTarget) return;
    try {
      const res = await axios.delete(`/api/batch-image/models/user/${encodeURIComponent(removeTarget.id)}`, {
        params: { delete_files: removeTarget.deleteFiles ? "1" : "0" },
      });
      if (res.data.success) {
        showMessage?.(`Removed ${removeTarget.name || removeTarget.id}.`, "info");
        fetchModels();
      } else {
        showMessage?.(res.data.error?.message || "Could not remove", "error");
      }
    } catch (err) {
      showMessage?.(formatUiError(err.response?.data?.error) || err.message || "Could not remove", "error");
    } finally {
      setRemoveTarget(null);
    }
  };

  const isDownloading = downloadStatus.is_downloading;
  const currentModel = downloadStatus.current_model;

  const editingMissing = editing.filter((p) => !p.installed);
  const editingMissingGb = editingMissing.reduce((sum, p) => sum + (p.download_gb || 0), 0);

  // One pack after another: the downloader takes one plan at a time.
  const waitForDownloadEnd = () =>
    new Promise((resolve) => {
      const check = setInterval(async () => {
        try {
          const res = await axios.get("/api/batch-image/models/download-status");
          if (res.data?.success && !res.data.data?.is_downloading) {
            clearInterval(check);
            resolve();
          }
        } catch {
          /* keep waiting */
        }
      }, 1000);
    });

  const handleInstallAllEditing = async () => {
    for (const pack of editingMissing) {
      await handleDownload(pack);
      await new Promise((r) => setTimeout(r, 1500));
      await waitForDownloadEnd();
    }
    fetchModels();
  };

  // Install / Installed / progress cell, shared by model rows and LoRA rows.
  const renderInstallCell = (model) => {
    const isThis = isDownloading && modelMatchesDownload(model, currentModel);
    if (isThis) {
      return (
        <Box sx={{ width: 130 }}>
          <Typography variant="caption" noWrap>
            {downloadStatus.status === "starting"
              ? "Starting..."
              : `${downloadStatus.progress}% \u2014 ${downloadStatus.speed_mbps} MB/s`}
          </Typography>
          <LinearProgress
            variant={downloadStatus.progress > 0 ? "determinate" : "indeterminate"}
            value={downloadStatus.progress}
            sx={{ mt: 0.5 }}
          />
          <Typography variant="caption" color="text.secondary">
            {(downloadStatus.downloaded_gb || 0).toFixed(1)} / {(downloadStatus.total_gb || 0).toFixed(1)} GB
          </Typography>
        </Box>
      );
    }
    return (
      <Box sx={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 0.5 }}>
        {model.is_downloaded ? (
          <Chip icon={<CheckCircleIcon />} label="Installed" color="success" size="small" variant="outlined" />
        ) : (
          <Button
            variant="outlined"
            size="small"
            startIcon={<CloudDownloadIcon />}
            onClick={() => handleDownload(model)}
            disabled={isDownloading}
          >
            Install
          </Button>
        )}
        {model.user && (
          <ActionButton kind="destructive" onClick={() => setRemoveTarget(model)} disabled={isDownloading}>
            Remove
          </ActionButton>
        )}
      </Box>
    );
  };

  return (
    <Dialog open={open} onClose={() => !isDownloading && onClose()} maxWidth="sm" fullWidth>
      <DialogTitle>Manage Image Generation Models</DialogTitle>
      <DialogContent dividers>
        {error && (
          <Box mb={2}>
            <Typography color="error">{error}</Typography>
          </Box>
        )}

        {loading ? (
          <Box display="flex" justifyContent="center" p={3}>
            <CircularProgress />
          </Box>
        ) : (
          <List disablePadding>
            {models.map((model) => {
              return (
                <ListItem key={model.id} divider sx={{ py: 1.5 }}>
                  <ListItemIcon>
                    <ImageIcon color={model.is_downloaded ? "primary" : "action"} />
                  </ListItemIcon>
                  <ListItemText
                    primary={
                      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                        <Typography variant="body1" fontWeight={500}>
                          {model.name || model.id}
                        </Typography>
                        {model.recommended && (
                          <Chip label="Recommended" size="small" color="primary" variant="outlined" />
                        )}
                        {model.user && <Chip label={`Yours · ${model.family || "model"}`} size="small" variant="outlined" />}
                        {model.size_gb > 0 && (
                          <Chip label={`${model.size_gb} GB`} size="small" variant="outlined" />
                        )}
                      </Box>
                    }
                    secondary={
                      <>
                        {model.description && (
                          <Typography variant="body2" color="text.secondary" component="span" display="block">
                            {model.description}
                          </Typography>
                        )}
                        <Typography variant="caption" color="text.disabled" component="span" display="block">
                          {model.path}
                        </Typography>
                      </>
                    }
                  />

                  <Box sx={{ ml: 2, minWidth: 120, textAlign: "right" }}>{renderInstallCell(model)}</Box>
                </ListItem>
              );
            })}
            {adapters.length > 0 && (
              <Typography variant="overline" color="text.secondary" sx={{ display: "block", mt: 2 }}>
                Your LoRAs
              </Typography>
            )}
            {adapters.map((a) => (
              <ListItem key={a.id} divider sx={{ py: 1.5 }}>
                <ListItemIcon>
                  <ImageIcon color={a.is_downloaded ? "primary" : "action"} />
                </ListItemIcon>
                <ListItemText
                  primary={
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <Typography variant="body1" fontWeight={500}>
                        {a.name}
                      </Typography>
                      <Chip label={`LoRA · ${a.family}`} size="small" variant="outlined" />
                      {a.size_gb > 0 && <Chip label={`${a.size_gb} GB`} size="small" variant="outlined" />}
                    </Box>
                  }
                  secondary={
                    <Typography variant="caption" color="text.disabled" component="span" display="block">
                      {a.hf_repo}
                    </Typography>
                  }
                />
                <Box sx={{ ml: 2, minWidth: 120, textAlign: "right" }}>{renderInstallCell(a)}</Box>
              </ListItem>
            ))}
            {editing.length > 0 && (
              <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mt: 2 }}>
                <Typography variant="overline" color="text.secondary">
                  Image editing
                </Typography>
                {editingMissing.length > 0 && (
                  <Button
                    size="small"
                    startIcon={<CloudDownloadIcon />}
                    onClick={handleInstallAllEditing}
                    disabled={isDownloading}
                  >
                    Install all editing tools ({editingMissingGb.toFixed(1)} GB)
                  </Button>
                )}
              </Box>
            )}
            {editing.map((pack) => (
              <ListItem key={pack.id} divider sx={{ py: 1.5 }}>
                <ListItemIcon>
                  <ImageIcon color={pack.installed ? "primary" : "action"} />
                </ListItemIcon>
                <ListItemText
                  primary={
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                      <Typography variant="body1" fontWeight={500}>
                        {pack.name}
                      </Typography>
                      {(pack.installed ? pack.size_gb : pack.download_gb) > 0 && (
                        <Chip
                          label={`${pack.installed ? pack.size_gb : pack.download_gb} GB`}
                          size="small"
                          variant="outlined"
                        />
                      )}
                    </Box>
                  }
                  secondary={
                    <>
                      <Typography variant="body2" color="text.secondary" component="span" display="block">
                        {pack.description}
                      </Typography>
                      <Typography variant="caption" color="text.disabled" component="span" display="block">
                        Chat: {(pack.tools || []).join(", ")}
                      </Typography>
                    </>
                  }
                />
                <Box sx={{ ml: 2, minWidth: 120, textAlign: "right" }}>{renderInstallCell(pack)}</Box>
              </ListItem>
            ))}
            {models.length === 0 && !loading && (
              <Typography variant="body2" color="textSecondary" align="center" sx={{ py: 3 }}>
                No models available in configuration.
              </Typography>
            )}
          </List>
        )}

        {/* FLUX.1-dev stills install from this list (ComfyUI UNET). Community
            FLUX UNETs and LoRAs add via Add new model. Schnell keyframe GGUF
            companions still live under Video Models. */}
        <Box sx={{ mt: 2, p: 1.5, borderRadius: 1, bgcolor: "action.hover" }}>
          <Typography variant="caption" color="text.secondary">
            <strong>FLUX.1-dev</strong> stills install here. Add another FLUX UNET or LoRA with
            Add new model. Keyframe GGUF packs (schnell) still live under{" "}
            <strong>Video Models</strong>.
          </Typography>
        </Box>
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={() => setAddOpen(true)} disabled={isDownloading}>
          Add new model
        </ActionButton>
        <Box sx={{ flex: 1 }} />
        <Button onClick={onClose} disabled={isDownloading}>
          {isDownloading ? "Downloading..." : "Close"}
        </Button>
      </DialogActions>
      <AddImageModelDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        showMessage={showMessage}
        onAdded={() => {
          fetchModels();
          fetchDownloadStatus();
        }}
      />
      <ConfirmActionDialog
        open={Boolean(removeTarget)}
        title={`Remove ${removeTarget?.name || ""}?`}
        description="Drops it from your catalog. Downloaded files stay unless you turn on Delete downloaded files."
        extra={
          <SettingChip
            label="Delete downloaded files"
            on={Boolean(removeTarget?.deleteFiles)}
            onToggle={(next) => setRemoveTarget((t) => (t ? { ...t, deleteFiles: next } : t))}
          />
        }
        keeps="the shipped Image Models list"
        confirmLabel="Remove"
        onClose={() => setRemoveTarget(null)}
        onConfirm={handleRemove}
      />
    </Dialog>
  );
};

export default ImageModelsModal;
