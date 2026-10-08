import React, { useState, useEffect, useRef } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Grid,
  InputAdornment,
  IconButton,
  Box,
  Chip,
  LinearProgress,
  Typography,
} from "@mui/material";
import CollapsibleAlert from "../common/CollapsibleAlert";
import FolderOpenIcon from "@mui/icons-material/FolderOpen";
import DirectoryPicker from "../common/DirectoryPicker";
import {
  getTrainingDatasetLocations,
  inspectTrainingDataset,
} from "../../api/trainingService";

const DATASET_EXTENSIONS = [".jsonl", ".json"];
// Typing pauses this long before the path is read.
const INSPECT_DELAY_MS = 300;

const FORMAT_LABELS = {
  messages: "chat messages",
  sharegpt: "ShareGPT conversations",
  prompt_completion: "prompt/completion",
  alpaca: "instruction/output",
  text: "plain text",
};

const baseName = (path) =>
  path.replace(/\/+$/, "").split("/").pop().replace(/\.(jsonl|json)$/i, "");

const InspectPanel = ({ report, inspecting }) => {
  if (inspecting) {
    return (
      <Box sx={{ mt: 1 }}>
        <LinearProgress />
        <Typography variant="caption" color="text.secondary">
          Reading the dataset…
        </Typography>
      </Box>
    );
  }
  if (!report) return null;
  if (!report.trainable) {
    return (
      <CollapsibleAlert severity="error" sx={{ mt: 1 }}>
        {report.reason ? `This cannot be trained on: ${report.reason}` : "This cannot be trained on."}
      </CollapsibleAlert>
    );
  }
  const formats = Object.entries(report.formats || {})
    .map(([fmt, n]) => `${n} ${FORMAT_LABELS[fmt] || fmt}`)
    .join(", ");
  const fileCount = report.files?.length || 0;
  return (
    <Box sx={{ mt: 1 }} data-testid="dataset-inspect">
      <CollapsibleAlert severity="success">
        {report.usable} of {report.rows} rows can be trained on ({formats}), in {fileCount} file
        {fileCount === 1 ? "" : "s"}.
      </CollapsibleAlert>
      {report.error_count > 0 && (
        <CollapsibleAlert severity="warning" sx={{ mt: 1 }}>
          {report.error_count} row{report.error_count === 1 ? "" : "s"} will be skipped:
          <Box component="ul" sx={{ m: 0, pl: 2 }}>
            {report.errors.map((e) => (
              <li key={e}>
                <Typography variant="caption">{e}</Typography>
              </li>
            ))}
          </Box>
        </CollapsibleAlert>
      )}
      {report.samples?.length > 0 && (
        <Box sx={{ mt: 1 }}>
          <Typography variant="caption" color="text.secondary">
            First rows as the trainer reads them
          </Typography>
          {report.samples.map((sample, i) => (
            <Box
              key={i}
              sx={{ mt: 0.5, p: 1, borderRadius: 1, bgcolor: "action.hover", fontSize: "0.75rem" }}
            >
              {sample.text !== undefined ? (
                <Typography variant="caption" sx={{ whiteSpace: "pre-wrap" }}>
                  {sample.text}
                </Typography>
              ) : (
                sample.messages.map((m, j) => (
                  <Box key={j} sx={{ display: "flex", gap: 1, alignItems: "baseline" }}>
                    <Chip label={m.role} size="small" variant="outlined" sx={{ height: 18, fontSize: "0.65rem" }} />
                    <Typography variant="caption" sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                      {m.content}
                    </Typography>
                  </Box>
                ))
              )}
            </Box>
          ))}
        </Box>
      )}
    </Box>
  );
};

/**
 * Add or edit a training dataset: a .jsonl or .json file, or a folder of them,
 * on this machine. The path is read as the trainer reads it, and Save waits
 * until it can be trained on.
 *
 * @param {object}  [datasetData]  the dataset being edited
 * @param {object}  [prefill]      starting values for a new dataset ({ name, path })
 */
const TrainingDatasetModal = ({
  open,
  onClose,
  datasetData,
  prefill,
  onSave,
  isSaving,
}) => {
  const [formData, setFormData] = useState({
    name: "",
    description: "",
    path: "",
  });
  const [formError, setFormError] = useState(null);
  const [directoryPickerOpen, setDirectoryPickerOpen] = useState(false);
  const [startPath, setStartPath] = useState("~");
  const [report, setReport] = useState(null);
  const [inspecting, setInspecting] = useState(false);
  const inspectId = useRef(0);

  useEffect(() => {
    if (open) {
      const source = datasetData || prefill || {};
      setFormData({
        name: source.name || "",
        description: source.description || "",
        path: source.path || "",
      });
      setFormError(null);
      setReport(null);
      getTrainingDatasetLocations()
        .then((locations) => setStartPath(locations?.default || "~"))
        .catch(() => setStartPath("~"));
    }
  }, [open, datasetData, prefill]);

  useEffect(() => {
    if (!open) return undefined;
    const path = formData.path.trim();
    const id = ++inspectId.current;
    if (!path) {
      setReport(null);
      setInspecting(false);
      return undefined;
    }
    setInspecting(true);
    const timer = setTimeout(() => {
      inspectTrainingDataset(path)
        .then((result) => {
          if (id === inspectId.current) setReport(result);
        })
        .catch((err) => {
          if (id === inspectId.current) {
            setReport({ trainable: false, reason: err.message || "the dataset could not be read" });
          }
        })
        .finally(() => {
          if (id === inspectId.current) setInspecting(false);
        });
    }, INSPECT_DELAY_MS);
    return () => clearTimeout(timer);
  }, [open, formData.path]);

  const handleInputChange = (e) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  // An existing dataset may keep the path it was saved with while it is
  // renamed; the server checks a changed path the same way.
  const pathUnchanged = Boolean(datasetData) && formData.path === (datasetData.path || "");
  const pathReady = pathUnchanged || (!inspecting && Boolean(report?.trainable));
  const canSave = !isSaving && Boolean(formData.name.trim()) && pathReady;

  const handleSave = () => {
    if (!formData.name.trim()) {
      setFormError("Dataset name is required.");
      return;
    }
    if (!pathReady) {
      setFormError("Choose a file or folder that can be trained on.");
      return;
    }
    setFormError(null);
    onSave({
      name: formData.name.trim(),
      description: formData.description,
      path: report?.trainable && report.path ? report.path : formData.path.trim(),
    });
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>
        {datasetData ? `Edit Dataset: ${datasetData.name}` : "Add New Dataset"}
      </DialogTitle>
      <DialogContent dividers>
        {formError && (
          <CollapsibleAlert severity="error" sx={{ mb: 2 }}>
            {formError}
          </CollapsibleAlert>
        )}
        <Grid container spacing={2} sx={{ mt: 0 }}>
          <Grid item xs={12}>
            <TextField
              autoFocus
              required
              fullWidth
              margin="dense"
              label="Dataset Name"
              name="name"
              value={formData.name}
              onChange={handleInputChange}
              disabled={isSaving}
            />
          </Grid>
          <Grid item xs={12}>
            <TextField
              fullWidth
              multiline
              minRows={2}
              margin="dense"
              label="Description"
              name="description"
              value={formData.description}
              onChange={handleInputChange}
              disabled={isSaving}
            />
          </Grid>
          <Grid item xs={12}>
            <TextField
              fullWidth
              required
              margin="dense"
              label="File or folder (.jsonl or .json)"
              name="path"
              value={formData.path}
              onChange={handleInputChange}
              disabled={isSaving}
              InputProps={{
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton
                      onClick={() => setDirectoryPickerOpen(true)}
                      edge="end"
                      disabled={isSaving}
                      title="Browse this machine"
                      aria-label="Browse this machine"
                    >
                      <FolderOpenIcon />
                    </IconButton>
                  </InputAdornment>
                ),
              }}
              helperText="A .jsonl or .json file on this machine, or a folder of them (searched inside)."
            />
            <InspectPanel report={report} inspecting={inspecting} />
          </Grid>
        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isSaving}>
          Cancel
        </Button>
        <Button onClick={handleSave} variant="contained" disabled={!canSave}>
          Save
        </Button>
      </DialogActions>

      <DirectoryPicker
        open={directoryPickerOpen}
        onClose={() => setDirectoryPickerOpen(false)}
        onSelect={(selectedPath) => {
          setFormData((prev) => ({
            ...prev,
            path: selectedPath,
            name: prev.name.trim() ? prev.name : baseName(selectedPath),
          }));
          setDirectoryPickerOpen(false);
        }}
        initialPath={formData.path.trim() || startPath}
        fallbackPath={startPath}
        mode="fileOrFolder"
        fileExtensions={DATASET_EXTENSIONS}
        title="Choose a dataset file or folder"
      />
    </Dialog>
  );
};

export default TrainingDatasetModal;
