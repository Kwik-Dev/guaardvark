// Add a Hugging Face weight to the user video catalog, then Install.
// Role is required: a LoRA on a shipped model, another UNET like one, or a
// text encoder that replaces the one a model ships with.

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
  { value: "lora", label: "LoRA on a model" },
  { value: "generation", label: "New generation model" },
  { value: "encoder", label: "Text encoder" },
];

const LIKE_LABEL = { lora: "Applies to", generation: "Like", encoder: "Replaces the encoder of" };

const AddVideoModelDialog = ({ open, onClose, models, showMessage, onAdded }) => {
  const [url, setUrl] = useState("");
  const [looking, setLooking] = useState(false);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");
  const [role, setRole] = useState("lora");
  const [like, setLike] = useState("");
  const [selected, setSelected] = useState([]);
  const [experts, setExperts] = useState({});
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);

  const generationModels = useMemo(
    () => (models || []).filter((m) => !m.user && ["wan", "minimax", "ltx", "hunyuan", "cogvideox"].includes(m.type)),
    [models],
  );
  // Only families whose graph takes a replacement encoder are offered for that role.
  const likeChoices = useMemo(
    () => (role === "encoder" ? generationModels.filter((m) => m.encoder_swap) : generationModels),
    [generationModels, role],
  );
  // Two-expert Wan 14B templates need a High and a Low file.
  const needsMoE = Boolean(like && /14b/.test(like) && role === "generation");

  useEffect(() => {
    if (!open) {
      setUrl("");
      setPreview(null);
      setError("");
      setRole("lora");
      setLike("");
      setSelected([]);
      setExperts({});
      setName("");
      setSaving(false);
    }
  }, [open]);

  useEffect(() => {
    if (preview?.suggested_role) setRole(preview.suggested_role);
    if (preview?.suggested_like) setLike(preview.suggested_like);
    if (preview?.src) {
      setSelected([preview.src]);
      setName(preview.src.split("/").pop().replace(/\.[^.]+$/, ""));
    } else if (preview?.files?.length === 1) {
      setSelected([preview.files[0].src]);
      setName(preview.files[0].src.split("/").pop().replace(/\.[^.]+$/, ""));
    }
  }, [preview]);

  const handleLookup = async () => {
    setError("");
    setLooking(true);
    try {
      const res = await axios.post("/api/batch-video/models/from-hf", { url });
      if (res.data.success) {
        setPreview(res.data.data);
      } else {
        setError(res.data.error?.message || res.data.message || "Lookup failed");
      }
    } catch (err) {
      const msg = err.response?.data?.error?.message || err.message || "Lookup failed";
      setError(msg);
    } finally {
      setLooking(false);
    }
  };

  const toggleFile = (src) => {
    setSelected((prev) => (prev.includes(src) ? prev.filter((s) => s !== src) : [...prev, src]));
  };

  const pending = Boolean(
    preview && like && selected.length > 0 && !saving && (role !== "encoder" || selected.length === 1),
  );

  const handleAdd = async () => {
    if (!pending) return;
    setSaving(true);
    setError("");
    try {
      const files = selected.map((src) => ({
        src,
        size: (preview.files || []).find((f) => f.src === src)?.size || 0,
        expert: experts[src] || undefined,
      }));
      const res = await axios.post("/api/batch-video/models/user", {
        url,
        hf_repo: preview.hf_repo,
        revision: preview.revision,
        role,
        like,
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
          Paste a Hugging Face URL or org/repo. The file lands in ComfyUI the same way Install already works.
          Pick whether it is a LoRA on a model you have, another UNET like one, or a text encoder that
          stands in for the one a model ships with.
        </Hint>
        <Box sx={{ display: "flex", gap: 1, alignItems: "flex-start" }}>
          <TextField
            size="small"
            fullWidth
            label="Hugging Face URL"
            placeholder="https://huggingface.co/org/repo/blob/main/file.safetensors"
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
              {preview.gated ? " · gated (needs HF_TOKEN)" : ""}
              {preview.truncated ? " · file list truncated" : ""}
            </Typography>
            <ChoiceChips
              ariaLabel="Role"
              value={role}
              onChange={setRole}
              options={ROLE_OPTIONS}
            />
            <FormControl size="small" fullWidth>
              <InputLabel>{LIKE_LABEL[role]}</InputLabel>
              <Select
                value={likeChoices.some((m) => m.id === like) ? like : ""}
                label={LIKE_LABEL[role]}
                onChange={(e) => setLike(e.target.value)}
              >
                {likeChoices.map((m) => (
                  <MenuItem key={m.id} value={m.id}>
                    {m.name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <TextField
              size="small"
              label="Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <Typography variant="overline" color="text.secondary">
              Files
            </Typography>
            <Box sx={{ maxHeight: 240, overflow: "auto", border: 1, borderColor: "divider", borderRadius: 1, px: 1 }}>
              {files.length === 0 && (
                <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
                  No weight files listed. Paste a URL that includes the filename.
                </Typography>
              )}
              {files.map((f) => (
                <Box key={f.src} sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <FormControlLabel
                    sx={{ flex: 1, mr: 0 }}
                    control={
                      <Checkbox
                        size="small"
                        checked={selected.includes(f.src)}
                        onChange={() => toggleFile(f.src)}
                      />
                    }
                    label={
                      <Typography variant="body2" sx={{ wordBreak: "break-all" }}>
                        {f.src}
                        {f.size ? ` (${(f.size / 1024 ** 3).toFixed(2)} GB)` : ""}
                      </Typography>
                    }
                  />
                  {needsMoE && selected.includes(f.src) && (
                    <Select
                      size="small"
                      value={experts[f.src] || ""}
                      onChange={(e) => setExperts((prev) => ({ ...prev, [f.src]: e.target.value }))}
                      displayEmpty
                      sx={{ minWidth: 88 }}
                    >
                      <MenuItem value="">auto</MenuItem>
                      <MenuItem value="high">High</MenuItem>
                      <MenuItem value="low">Low</MenuItem>
                    </Select>
                  )}
                </Box>
              ))}
            </Box>
            {needsMoE && (
              <Hint>This family is two experts. Pick one HighNoise file and one LowNoise file.</Hint>
            )}
            {role === "encoder" && (
              <Hint>
                One file. It replaces the shipped text encoder for every model in that family; pick it on the
                Video Gen page under Text encoder. The shipped one stays installed.
              </Hint>
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

export default AddVideoModelDialog;
