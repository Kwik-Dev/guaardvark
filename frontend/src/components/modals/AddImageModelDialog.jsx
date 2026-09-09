// Add a Hugging Face image model or LoRA to the user image catalog, then Install.
// Role is required: a new generation model of a shipped family (a diffusers repo,
// or a single SD / SDXL checkpoint) or a LoRA the offline engine stacks on one.

import React, { useEffect, useMemo, useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Checkbox,
  FormControlLabel,
  Typography,
  Box,
  LinearProgress,
} from "@mui/material";
import axios from "axios";
import { ActionButton, ChoiceChips, Hint } from "../settings/ui";

const ROLE_OPTIONS = [
  { value: "generation", label: "New generation model" },
  { value: "lora", label: "LoRA on a model" },
];

const AddImageModelDialog = ({ open, onClose, showMessage, onAdded }) => {
  const [url, setUrl] = useState("");
  const [looking, setLooking] = useState(false);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");
  const [role, setRole] = useState("generation");
  const [family, setFamily] = useState("");
  const [selected, setSelected] = useState([]);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);

  const families = preview?.families || [];
  // A LoRA only where the engine stacks it; a single checkpoint only where
  // diffusers can rebuild the pipeline from one file.
  const familyChoices = useMemo(() => {
    if (role === "lora") return families.filter((f) => f.lora);
    if (preview && !preview.has_model_index) return families.filter((f) => f.single_file);
    return families;
  }, [families, role, preview]);
  const snapshot = Boolean(preview?.has_model_index && role === "generation");

  useEffect(() => {
    if (!open) {
      setUrl("");
      setPreview(null);
      setError("");
      setRole("generation");
      setFamily("");
      setSelected([]);
      setName("");
      setSaving(false);
    }
  }, [open]);

  useEffect(() => {
    if (preview?.suggested_role) setRole(preview.suggested_role);
    if (preview?.suggested_family) setFamily(preview.suggested_family);
    if (preview?.src) {
      setSelected([preview.src]);
      setName(preview.src.split("/").pop().replace(/\.[^.]+$/, ""));
    } else if (preview?.files?.length === 1) {
      setSelected([preview.files[0].src]);
      setName(preview.files[0].src.split("/").pop().replace(/\.[^.]+$/, ""));
    } else if (preview?.hf_repo) {
      setName(preview.hf_repo.split("/").pop());
    }
  }, [preview]);

  const handleLookup = async () => {
    setError("");
    setLooking(true);
    try {
      const res = await axios.post("/api/batch-image/models/from-hf", { url });
      if (res.data.success) {
        setPreview(res.data.data);
      } else {
        setError(res.data.error?.message || res.data.message || "Lookup failed");
      }
    } catch (err) {
      setError(err.response?.data?.error?.message || err.message || "Lookup failed");
    } finally {
      setLooking(false);
    }
  };

  const toggleFile = (src) => {
    setSelected((prev) => (prev.includes(src) ? prev.filter((s) => s !== src) : [...prev, src]));
  };

  const familyOk = familyChoices.some((f) => f.id === family);
  const pending = Boolean(
    preview && familyOk && !saving && (snapshot || selected.length === 1),
  );

  const handleAdd = async () => {
    if (!pending) return;
    setSaving(true);
    setError("");
    try {
      const files = snapshot
        ? []
        : selected.map((src) => ({
            src,
            size: (preview.files || []).find((f) => f.src === src)?.size || 0,
          }));
      const res = await axios.post("/api/batch-image/models/user", {
        url,
        hf_repo: preview.hf_repo,
        revision: preview.revision,
        has_model_index: Boolean(preview.has_model_index),
        role,
        family,
        files,
        name: name.trim() || undefined,
        install: true,
      });
      if (!res.data.success) {
        setError(res.data.error?.message || res.data.message || "Could not add");
        setSaving(false);
        return;
      }
      showMessage?.(`Added ${res.data.data?.entry?.name || name}. Installing…`, "info");
      onAdded?.(res.data.data?.id);
      onClose();
    } catch (err) {
      setError(err.response?.data?.error?.message || err.message || "Could not add");
    } finally {
      setSaving(false);
    }
  };

  const files = preview?.files || [];

  return (
    <Dialog open={open} onClose={saving ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add new model</DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1.5, pt: 1 }}>
        <Hint>
          Paste a Hugging Face URL or org/repo. A diffusers repo installs whole; a single
          .safetensors installs as one checkpoint (SD and SDXL) or as a LoRA. FLUX and other
          ComfyUI models belong in Manage Video Models.
        </Hint>
        <Box sx={{ display: "flex", gap: 1, alignItems: "flex-start" }}>
          <TextField
            size="small"
            fullWidth
            label="Hugging Face URL"
            placeholder="https://huggingface.co/org/repo"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleLookup()}
            disabled={looking || saving}
          />
          <ActionButton onClick={handleLookup} loading={looking} disabled={!url.trim()}>
            Look up
          </ActionButton>
        </Box>
        {looking && <LinearProgress />}
        {error && (
          <Typography variant="body2" color="error">
            {error}
          </Typography>
        )}
        {preview && (
          <>
            <Typography variant="caption" color="text.secondary">
              {preview.hf_repo}
              {preview.has_model_index ? " · diffusers repo" : " · files only"}
              {preview.gated ? " · gated (needs HF_TOKEN)" : ""}
            </Typography>
            <ChoiceChips ariaLabel="Role" value={role} onChange={setRole} options={ROLE_OPTIONS} />
            <FormControl size="small" fullWidth>
              <InputLabel>{role === "lora" ? "Applies to" : "Family"}</InputLabel>
              <Select
                value={familyOk ? family : ""}
                label={role === "lora" ? "Applies to" : "Family"}
                onChange={(e) => setFamily(e.target.value)}
              >
                {familyChoices.map((f) => (
                  <MenuItem key={f.id} value={f.id}>
                    {f.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            {familyChoices.length === 0 && (
              <Typography variant="body2" color="error">
                {role === "lora"
                  ? "LoRAs stack on Z-Image today."
                  : "Z-Image and Krea 2 install from a diffusers repo; this repo has no model_index.json."}
              </Typography>
            )}
            <TextField size="small" label="Name" value={name} onChange={(e) => setName(e.target.value)} />
            {snapshot ? (
              <Hint>The whole repo installs, the same way the shipped models do.</Hint>
            ) : (
              <>
                <Typography variant="overline" color="text.secondary">
                  Files (pick one)
                </Typography>
                <Box sx={{ maxHeight: 240, overflow: "auto", border: 1, borderColor: "divider", borderRadius: 1, px: 1 }}>
                  {files.length === 0 && (
                    <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
                      No weight files listed. Paste a URL that includes the filename.
                    </Typography>
                  )}
                  {files.map((f) => (
                    <FormControlLabel
                      key={f.src}
                      sx={{ display: "flex", mr: 0 }}
                      control={
                        <Checkbox size="small" checked={selected.includes(f.src)} onChange={() => toggleFile(f.src)} />
                      }
                      label={
                        <Typography variant="body2" sx={{ wordBreak: "break-all" }}>
                          {f.src}
                          {f.size ? ` (${(f.size / 1024 ** 3).toFixed(2)} GB)` : ""}
                        </Typography>
                      }
                    />
                  ))}
                </Box>
              </>
            )}
          </>
        )}
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={onClose} disabled={saving}>
          Cancel
        </ActionButton>
        <ActionButton kind="primary" onClick={handleAdd} loading={saving} disabled={!pending}>
          Add and install
        </ActionButton>
      </DialogActions>
    </Dialog>
  );
};

export default AddImageModelDialog;
