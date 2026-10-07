// frontend/src/components/modals/TrainingLibrariesModal.jsx
// Settings > Training libraries: the optional Python libraries fine-tuning
// needs (Unsloth, TRL, Datasets). Lists them at their pinned versions with
// download sizes, installs them only on the Install click, shows pip's
// progress and result, and removes them again. On a machine the hardware
// policy calls not practical, the only install button is "Install anyway".
// Below them, the declared base models a fine-tune starts from, each
// downloaded or removed only on its own button.

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
  Alert,
  Divider,
  Stack,
} from "@mui/material";
import ModelTrainingIcon from "@mui/icons-material/ModelTraining";
import CloudDownloadIcon from "@mui/icons-material/CloudDownload";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import { ActionButton, ConfirmActionDialog } from "../settings/ui";
import {
  getBaseModels,
  getTrainingLibraries,
  installTrainingBaseModel,
  installTrainingLibraries,
  removeTrainingBaseModel,
  removeTrainingLibraries,
} from "../../api/trainingService";

const RESTART_HINT =
  "Restart Guaardvark (Settings > Danger zone > Reboot) before training, so the trainer loads the new libraries.";

const sizeLabel = (mb) => {
  const n = Number(mb) || 0;
  if (n <= 0) return null;
  if (n >= 1000) return `${(n / 1000).toFixed(1)} GB`;
  return `${n < 10 ? n.toFixed(1) : Math.round(n)} MB`;
};

const stateChip = (lib) => {
  if (lib.state === "installed") {
    return (
      <Chip icon={<CheckCircleIcon />} label="Installed" color="success" size="small" variant="outlined" />
    );
  }
  if (lib.state === "other_version") {
    return (
      <Chip
        label={`${lib.installed_version} installed`}
        color="warning"
        size="small"
        variant="outlined"
        title={`Pinned: ${lib.version}`}
      />
    );
  }
  return <Chip label="Not installed" size="small" variant="outlined" />;
};

/**
 * @param {boolean}  open
 * @param {function} onClose
 * @param {function} [showMessage]  snackbar: (message, severity)
 * @param {function} [onChanged]    called with the new status when an install or remove ends
 * @param {function} [onBaseModelsChanged]  called with the base-model status after a download or remove
 */
const TrainingLibrariesModal = ({ open, onClose, showMessage, onChanged, onBaseModelsChanged }) => {
  const [info, setInfo] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // Why the last Install or Remove click was refused; kept until the next click.
  const [actionError, setActionError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const lastRunState = useRef(null);

  const [baseInfo, setBaseInfo] = useState(null);
  const [baseError, setBaseError] = useState(null);
  const [baseBusy, setBaseBusy] = useState(false);
  const [confirmRemoveModel, setConfirmRemoveModel] = useState(null);
  const lastDownloadState = useRef(null);

  // The parent passes a fresh showMessage/onChanged on every render; keep them
  // in refs so the fetch callbacks stay stable and polling does not restart.
  const showMessageRef = useRef(showMessage);
  const onChangedRef = useRef(onChanged);
  const onBaseModelsChangedRef = useRef(onBaseModelsChanged);
  useEffect(() => {
    showMessageRef.current = showMessage;
    onChangedRef.current = onChanged;
    onBaseModelsChangedRef.current = onBaseModelsChanged;
  }, [showMessage, onChanged, onBaseModelsChanged]);

  const fetchBaseModels = useCallback(async () => {
    try {
      const data = await getBaseModels();
      setBaseInfo(data);
      setBaseError(null);
      const download = data?.download || {};
      if (lastDownloadState.current === "running" && download.state !== "running") {
        if (download.state === "completed") {
          showMessageRef.current?.(`${download.model} downloaded.`, "success");
        } else if (download.state === "failed") {
          showMessageRef.current?.(`${download.model} download failed: ${download.error}`, "error");
        }
        onBaseModelsChangedRef.current?.(data);
      }
      lastDownloadState.current = download.state;
    } catch (err) {
      setBaseError(err.message || "Could not read the base models.");
    }
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await getTrainingLibraries();
      setInfo(data);
      setError(null);
      const state = data?.run?.state;
      if (lastRunState.current === "running" && state !== "running") {
        const removing = data.run.action === "remove";
        if (state === "completed") {
          showMessageRef.current?.(
            removing ? "Training libraries removed." : "Training libraries installed.",
            "success",
          );
        } else if (state === "failed") {
          showMessageRef.current?.(
            `Training libraries ${removing ? "remove" : "install"} failed: ${data.run.error || "unknown error"}`,
            "error",
          );
        }
        onChangedRef.current?.(data);
      }
      lastRunState.current = state;
    } catch (err) {
      setError(err.message || "Could not read the training libraries.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      setLoading(true);
      lastRunState.current = null;
      lastDownloadState.current = null;
      fetchStatus();
      fetchBaseModels();
    } else {
      setInfo(null);
      setError(null);
      setActionError(null);
      setConfirmRemove(false);
      setBaseInfo(null);
      setBaseError(null);
      setConfirmRemoveModel(null);
    }
  }, [open, fetchStatus, fetchBaseModels]);

  const running = info?.run?.state === "running";
  const downloading = baseInfo?.download?.state === "running";

  useEffect(() => {
    if (!open || !running) return undefined;
    const interval = setInterval(fetchStatus, 1000);
    return () => clearInterval(interval);
  }, [open, running, fetchStatus]);

  useEffect(() => {
    if (!open || !downloading) return undefined;
    const interval = setInterval(fetchBaseModels, 1000);
    return () => clearInterval(interval);
  }, [open, downloading, fetchBaseModels]);

  // Like the library buttons: each click asks for its own plan token first.
  const runBaseAction = async (action, model) => {
    setBaseBusy(true);
    setBaseError(null);
    try {
      const plan = await getBaseModels({ plan: true });
      const data =
        action === "download"
          ? await installTrainingBaseModel(plan.plan_token, model.id)
          : await removeTrainingBaseModel(plan.plan_token, model.id);
      lastDownloadState.current = data?.download?.state || null;
      setBaseInfo(data);
      if (action === "remove") {
        showMessageRef.current?.(`${model.name} removed.`, "success");
        onBaseModelsChangedRef.current?.(data);
      }
    } catch (err) {
      const message = err.message || `Could not ${action} ${model.name}.`;
      showMessageRef.current?.(message, "error");
      setBaseError(message);
      fetchBaseModels();
    } finally {
      setBaseBusy(false);
      setConfirmRemoveModel(null);
    }
  };

  // Each click asks for its own plan token, then sends it with the request;
  // the backend runs pip only for a request that carries one.
  const runAction = async (action, options = {}) => {
    setBusy(true);
    setActionError(null);
    try {
      const plan = await getTrainingLibraries({ plan: true });
      const data =
        action === "install"
          ? await installTrainingLibraries(plan.plan_token, options)
          : await removeTrainingLibraries(plan.plan_token);
      lastRunState.current = data?.run?.state || "running";
      setInfo(data);
    } catch (err) {
      const message = err.message || `Could not start the ${action}.`;
      showMessageRef.current?.(message, "error");
      setActionError(message);
      fetchStatus();
    } finally {
      setBusy(false);
      setConfirmRemove(false);
    }
  };

  const libraries = info?.libraries || [];
  const hardware = info?.hardware || {};
  const missing = libraries.filter((l) => l.state === "missing");
  const otherVersions = libraries.filter((l) => l.state === "other_version");
  const unmet = info?.unmet || [];
  const removal = info?.removal || { remove: [], restore: {} };
  const restoreList = Object.entries(removal.restore || {});
  const run = info?.run || {};
  const notPractical = Boolean(info) && hardware.practical === false;

  // The install button's words; on a machine the hardware policy calls not
  // practical every form says "anyway", so choosing it is the insisting.
  const anyway = notPractical ? " anyway" : "";
  let installLabel = null;
  if (missing.length > 0) {
    const size = sizeLabel(info?.download_mb);
    installLabel = size ? `Install${anyway} (about ${size})` : `Install${anyway}`;
  } else if (unmet.length > 0) {
    installLabel = `Repair${anyway}`;
  } else if (otherVersions.length > 0) {
    installLabel = `Install pinned versions${anyway}`;
  }

  const hardwareLine = () => {
    if (!info) return null;
    const parts = [];
    if (hardware.gpu) parts.push(hardware.gpu);
    if (hardware.vram_mb) parts.push(`${(hardware.vram_mb / 1024).toFixed(0)} GB VRAM`);
    if (hardware.ram_gb) parts.push(`${hardware.ram_gb} GB RAM`);
    return parts.join(", ");
  };

  return (
    <Dialog open={open} onClose={() => !running && !busy && onClose()} maxWidth="sm" fullWidth>
      <DialogTitle>Training libraries</DialogTitle>
      <DialogContent dividers>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Optional. Fine-tuning on the Training page needs these Python libraries. Nothing is
          installed until you click Install; they go into the Python environment Guaardvark&apos;s
          backend runs in.
        </Typography>

        {(error || actionError) && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {actionError || error}
          </Alert>
        )}

        {notPractical && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            <strong>Training is not practical on this machine.</strong> {hardware.reason} Nothing
            installs unless you choose Install anyway.
          </Alert>
        )}
        {info && hardware.practical && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
            This machine: {hardwareLine() || hardware.reason}
          </Typography>
        )}

        {loading && !info ? (
          <Box display="flex" justifyContent="center" p={3}>
            <CircularProgress />
          </Box>
        ) : (
          <List disablePadding>
            {libraries.map((lib) => (
              <ListItem key={lib.id} divider sx={{ py: 1.5 }}>
                <ListItemIcon>
                  <ModelTrainingIcon color={lib.state === "installed" ? "primary" : "action"} />
                </ListItemIcon>
                <ListItemText
                  primary={
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                      <Typography variant="body1" fontWeight={500}>
                        {lib.name}
                      </Typography>
                      <Chip
                        label={lib.version}
                        size="small"
                        variant="outlined"
                        title={lib.pinned_because || ""}
                        sx={{ height: 20, fontSize: "0.65rem" }}
                      />
                      {lib.state !== "installed" && sizeLabel(lib.download_mb) && (
                        <Chip
                          label={sizeLabel(lib.download_mb)}
                          size="small"
                          variant="outlined"
                          title={lib.download_includes ? `Includes ${lib.download_includes}` : ""}
                        />
                      )}
                    </Box>
                  }
                  secondary={lib.role}
                />
                <Box sx={{ ml: 2, minWidth: 110, textAlign: "right" }}>{stateChip(lib)}</Box>
              </ListItem>
            ))}
          </List>
        )}

        {unmet.length > 0 && !running && (
          <Alert severity="warning" sx={{ mt: 2 }}>
            Installed, but incomplete. Repair installs what is missing:
            <Box component="ul" sx={{ m: 0, pl: 2 }}>
              {unmet.map((u) => (
                <li key={u}>
                  <Typography variant="caption">{u}</Typography>
                </li>
              ))}
            </Box>
          </Alert>
        )}

        {info && installLabel && (info.changes?.length > 0 || info.left_out?.length > 0) && (
          <Box sx={{ mt: 2, p: 1.5, borderRadius: 1, bgcolor: "action.hover" }}>
            {info.changes?.map((c) => (
              <Typography key={c.name} variant="caption" color="text.secondary" sx={{ display: "block" }}>
                Also moves <strong>{c.name}</strong> {c.installed} to {c.needs}: {c.why}. Remove puts it back.
              </Typography>
            ))}
            {info.left_out?.map((l) => (
              <Typography key={l.name} variant="caption" color="text.secondary" sx={{ display: "block" }}>
                Leaves out <strong>{l.name}</strong>: {l.why}
              </Typography>
            ))}
          </Box>
        )}

        {(running || run.state === "completed" || run.state === "failed") && (
          <Box sx={{ mt: 2 }}>
            {running && (
              <>
                <Typography variant="body2" noWrap>
                  {run.action === "remove" ? "Removing" : "Installing"}: {run.phase}
                </Typography>
                <LinearProgress
                  variant={run.progress > 1 ? "determinate" : "indeterminate"}
                  value={run.progress || 0}
                  sx={{ mt: 0.5, mb: 1 }}
                />
              </>
            )}
            {run.state === "completed" && (
              <Alert severity="success" sx={{ mb: 1 }}>
                {run.action === "remove" ? "Removed." : "Installed."} {RESTART_HINT}
                {run.check?.ok && (
                  <Typography variant="caption" sx={{ display: "block" }}>
                    Trainer check: {run.check.cuda_device || "GPU"}, bf16 {run.check.bf16 ? "yes" : "no"}, TRL{" "}
                    {run.check.libraries?.trl}, Unsloth {run.check.libraries?.unsloth}.
                  </Typography>
                )}
              </Alert>
            )}
            {run.pip_check_new?.length > 0 && (
              <Alert severity="warning" sx={{ mb: 1 }}>
                pip check reports problems this install introduced:
                <Box component="ul" sx={{ m: 0, pl: 2 }}>
                  {run.pip_check_new.map((line) => (
                    <li key={line}>
                      <Typography variant="caption">{line}</Typography>
                    </li>
                  ))}
                </Box>
              </Alert>
            )}
            {run.state === "failed" && (
              <Alert severity="error" sx={{ mb: 1 }}>
                {run.error || "Failed."}
              </Alert>
            )}
            {run.log?.length > 0 && (
              <Box
                sx={{
                  maxHeight: 180,
                  overflow: "auto",
                  p: 1,
                  borderRadius: 1,
                  bgcolor: "action.hover",
                  fontFamily: "monospace",
                  fontSize: "0.7rem",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                }}
              >
                {run.log.join("\n")}
              </Box>
            )}
          </Box>
        )}
        {run.restart_needed && !running && run.state !== "completed" && (
          <Alert severity="info" sx={{ mt: 2 }}>
            {RESTART_HINT}
          </Alert>
        )}

        <Divider sx={{ my: 2 }} />
        <Typography variant="subtitle1" fontWeight={500}>
          Base models
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          The models a fine-tune starts from. Download fetches one from huggingface.co when you
          click it, without your Hugging Face token; training itself never downloads.
        </Typography>
        {baseError && (
          <Alert severity="error" sx={{ mb: 1 }}>
            {baseError}
          </Alert>
        )}
        {baseInfo?.download?.state === "failed" && (
          <Alert severity="error" sx={{ mb: 1 }}>
            {baseInfo.download.model} did not download: {baseInfo.download.error}
          </Alert>
        )}
        {!baseInfo && !baseError ? (
          <Box display="flex" justifyContent="center" p={2}>
            <CircularProgress size={24} />
          </Box>
        ) : (
          <List disablePadding>
            {(baseInfo?.models || []).map((model) => {
              const active = downloading && baseInfo.download.model === model.id;
              return (
                <ListItem key={model.id} divider sx={{ py: 1.5, alignItems: "flex-start" }}>
                  <ListItemText
                    primary={
                      <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                        <Typography variant="body1" fontWeight={500}>
                          {model.name}
                        </Typography>
                        <Chip label={sizeLabel(model.size_gb * 1000)} size="small" variant="outlined" />
                        <Chip label={model.license} size="small" variant="outlined" />
                      </Box>
                    }
                    secondaryTypographyProps={{ component: "div" }}
                    secondary={
                      <>
                        {model.description}
                        {!model.fits && (
                          <Typography variant="caption" color="warning.main" sx={{ display: "block" }}>
                            Does not fit: {model.fit_reason}
                          </Typography>
                        )}
                        {active && (
                          <Box sx={{ mt: 1 }}>
                            <LinearProgress
                              variant={baseInfo.download.progress > 0 ? "determinate" : "indeterminate"}
                              value={baseInfo.download.progress || 0}
                            />
                            <Typography variant="caption">
                              {baseInfo.download.downloaded_gb || 0} of {model.size_gb} GB
                            </Typography>
                          </Box>
                        )}
                      </>
                    }
                  />
                  <Box sx={{ ml: 2, minWidth: 130, textAlign: "right" }}>
                    {model.installed ? (
                      <Stack spacing={1} alignItems="flex-end">
                        <Chip icon={<CheckCircleIcon />} label="Downloaded" color="success" size="small" variant="outlined" />
                        <ActionButton
                          kind="destructive"
                          onClick={() => setConfirmRemoveModel(model)}
                          disabled={baseBusy || downloading}
                        >
                          Remove
                        </ActionButton>
                      </Stack>
                    ) : model.fits ? (
                      <Button
                        variant="outlined"
                        size="small"
                        startIcon={<CloudDownloadIcon />}
                        onClick={() => runBaseAction("download", model)}
                        disabled={baseBusy || downloading}
                      >
                        {active ? "Downloading..." : "Download"}
                      </Button>
                    ) : (
                      <Chip label="Does not fit" size="small" variant="outlined" />
                    )}
                  </Box>
                </ListItem>
              );
            })}
          </List>
        )}
      </DialogContent>
      <DialogActions>
        {removal.remove?.length > 0 && (
          <ActionButton kind="destructive" onClick={() => setConfirmRemove(true)} disabled={running || busy}>
            Remove
          </ActionButton>
        )}
        <Box sx={{ flex: 1 }} />
        {installLabel && (
          <Button
            variant="outlined"
            color={notPractical ? "warning" : "primary"}
            size="small"
            startIcon={<CloudDownloadIcon />}
            onClick={() => runAction("install", { anyway: notPractical })}
            disabled={running || busy}
          >
            {installLabel}
          </Button>
        )}
        <Button onClick={onClose} disabled={running || busy}>
          {running ? (run.action === "remove" ? "Removing..." : "Installing...") : "Close"}
        </Button>
      </DialogActions>
      <ConfirmActionDialog
        open={confirmRemove}
        title="Remove the training libraries?"
        description="Uninstalls them from Guaardvark's Python environment. Training jobs refuse to start until they are installed again."
        facts={[
          { label: "Uninstalls", value: (removal.remove || []).join(", ") || "nothing" },
          ...(restoreList.length > 0
            ? [
                {
                  label: "Puts back (downloads)",
                  value: restoreList.map(([name, version]) => `${name} ${version}`).join(", "),
                },
              ]
            : []),
        ]}
        keeps="trained adapters, exported models and training datasets"
        confirmLabel="Remove"
        busy={busy}
        onConfirm={() => runAction("remove")}
        onClose={() => !busy && setConfirmRemove(false)}
      />
      <ConfirmActionDialog
        open={Boolean(confirmRemoveModel)}
        title={`Remove ${confirmRemoveModel?.name || "this base model"}?`}
        description="Deletes its weights from this machine. Jobs on it refuse to start until it is downloaded again."
        facts={[{ label: "Frees about", value: sizeLabel((confirmRemoveModel?.size_gb || 0) * 1000) || "-" }]}
        keeps="trained adapters, exported models and training datasets"
        confirmLabel="Remove"
        busy={baseBusy}
        onConfirm={() => runBaseAction("remove", confirmRemoveModel)}
        onClose={() => !baseBusy && setConfirmRemoveModel(null)}
      />
    </Dialog>
  );
};

export default TrainingLibrariesModal;
