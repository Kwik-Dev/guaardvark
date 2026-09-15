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
  Radio,
  RadioGroup,
  FormControlLabel,
  Typography,
  Box,
  LinearProgress,
} from "@mui/material";
import axios from "axios";
import { ActionButton, ChoiceChips, Hint } from "../settings/ui";
import useHfModelLookup from "../../hooks/useHfModelLookup";
import { formatWeightSize, selectedBytes } from "../../utils/hfUrl";

const ROLE_OPTIONS = [
  { value: "lora", label: "LoRA on a model" },
  { value: "generation", label: "New generation model" },
  { value: "encoder", label: "Text encoder" },
];

const LIKE_LABEL = { lora: "Applies to", generation: "Like", encoder: "Replaces the encoder of" };

const AddVideoModelDialog = ({ open, onClose, models, showMessage, onAdded }) => {
  const { url, onUrlChange, looking, preview, error, setError, handleLookup } = useHfModelLookup({
    endpoint: "/api/batch-video/models/from-hf",
    open,
  });
  const [role, setRole] = useState("lora");
  const [like, setLike] = useState("");
  const [selected, setSelected] = useState([]);
  const [experts, setExperts] = useState({});
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [fileFilter, setFileFilter] = useState("");

  const generationModels = useMemo(
    () => (models || []).filter((m) => !m.user && ["wan", "minimax", "ltx", "hunyuan", "cogvideox"].includes(m.type)),
    [models],
  );
  const likeChoices = useMemo(() => {
    if (role === "encoder") return generationModels.filter((m) => m.encoder_swap);
    if (role === "lora") return generationModels.filter((m) => m.lora_stack);
    return generationModels;
  }, [generationModels, role]);
  const needsMoE = Boolean(like && /14b/.test(like) && role === "generation");
  // Radio for one file (LoRA, encoder, a single UNET). Checkboxes only for a MoE pair.
  const singleFile = !needsMoE;
  const unwired = preview?.unwired;

  useEffect(() => {
    if (!open) {
      setRole("lora");
      setLike("");
      setSelected([]);
      setExperts({});
      setName("");
      setSaving(false);
      setFileFilter("");
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
    } else if (preview?.hf_repo) {
      setName(preview.hf_repo.split("/").pop());
    }
  }, [preview]);

  const toggleFile = (src) => {
    if (singleFile) {
      setSelected([src]);
      return;
    }
    setSelected((prev) => (prev.includes(src) ? prev.filter((s) => s !== src) : [...prev, src]));
  };

  const moeComplete = !needsMoE || (() => {
    const tags = selected.map((src) => experts[src]).filter(Boolean);
    const autoHigh = selected.filter((src) => /high/i.test(src) && !experts[src]);
    const autoLow = selected.filter((src) => /low/i.test(src) && !experts[src]);
    const highs = tags.filter((t) => t === "high").length + autoHigh.length;
    const lows = tags.filter((t) => t === "low").length + autoLow.length;
    return highs === 1 && lows === 1;
  })();

  const pending = Boolean(
    preview &&
      !unwired &&
      like &&
      likeChoices.some((m) => m.id === like) &&
      selected.length > 0 &&
      !saving &&
      (role !== "encoder" || selected.length === 1) &&
      (!singleFile || selected.length === 1) &&
      moeComplete &&
      (!needsMoE || selected.length === 2),
  );

  const handleAdd = async (install) => {
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
        install,
      });
      if (!res.data.success) {
        setError(res.data.error?.message || res.data.message || "Could not add");
        setSaving(false);
        return;
      }
      const problems = res.data.data?.verify || [];
      const dl = res.data.data?.download;
      const label = res.data.data?.entry?.name || name;
      if (problems.length) {
        showMessage?.(`Added ${label}, with registry notes: ${problems.join("; ")}`, "warning");
      } else if (dl?.error) {
        showMessage?.(`Added ${label}. Install did not start: ${dl.error}`, "warning");
      } else if (install) {
        showMessage?.(`Added ${label}. Installing…`, "info");
      } else {
        showMessage?.(`Added ${label}. Install when you are ready.`, "info");
      }
      onAdded?.(res.data.data?.id);
      onClose();
    } catch (err) {
      setError(err.response?.data?.error?.message || err.message || "Could not add");
    } finally {
      setSaving(false);
    }
  };

  const files = preview?.files || [];
  const visibleFiles = fileFilter.trim()
    ? files.filter((f) => f.src.toLowerCase().includes(fileFilter.trim().toLowerCase()))
    : files;
  const bytes = selectedBytes(files, selected);
  const repoUrl = preview?.hf_repo ? `https://huggingface.co/${preview.hf_repo}` : "";
  const likeName = likeChoices.find((m) => m.id === like)?.name || like;

  const fileRow = (f) => (
    <Box key={f.src} sx={{ display: "flex", alignItems: "center", gap: 1 }}>
      {singleFile ? null : (
        <FormControlLabel
          sx={{ flex: 1, mr: 0 }}
          control={
            <Checkbox size="small" checked={selected.includes(f.src)} onChange={() => toggleFile(f.src)} />
          }
          label={
            <Typography variant="body2" sx={{ wordBreak: "break-all" }}>
              {f.src}
              {formatWeightSize(f.size)}
            </Typography>
          }
        />
      )}
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
  );

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
            onChange={(e) => onUrlChange(e.target.value)}
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
        {preview && unwired && (
          <Typography variant="body2" color="error">
            {unwired.reason}
          </Typography>
        )}
        {preview && !unwired && (
          <>
            <Typography variant="caption" color="text.secondary">
              {preview.hf_repo}
              {preview.license ? ` · ${preview.license}` : ""}
              {preview.gated
                ? preview.token_present
                  ? " · gated (agree on the model page with your HF_TOKEN account)"
                  : " · gated (set HF_TOKEN in .env and restart)"
                : ""}
              {preview.truncated ? " · file list truncated" : ""}
            </Typography>
            {(preview.warnings || []).map((w) => (
              <Typography key={w} variant="caption" color="warning.main">
                {w}
              </Typography>
            ))}
            <ChoiceChips ariaLabel="Role" value={role} onChange={setRole} options={ROLE_OPTIONS} />
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
            {likeChoices.length === 0 && (
              <Typography variant="body2" color="error">
                No shipped model in this list takes that role.
              </Typography>
            )}
            <TextField size="small" label="Name" value={name} onChange={(e) => setName(e.target.value)} />
            <Typography variant="overline" color="text.secondary">
              {singleFile ? "Files (pick one)" : "Files"}
            </Typography>
            {files.length > 8 && (
              <TextField
                size="small"
                label="Filter files"
                value={fileFilter}
                onChange={(e) => setFileFilter(e.target.value)}
              />
            )}
            <Box sx={{ maxHeight: 240, overflow: "auto", border: 1, borderColor: "divider", borderRadius: 1, px: 1 }}>
              {files.length === 0 && (
                <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
                  No weight files listed. Paste a URL that includes the filename.
                </Typography>
              )}
              {singleFile ? (
                <RadioGroup value={selected[0] || ""} onChange={(e) => toggleFile(e.target.value)}>
                  {visibleFiles.map((f) => (
                    <FormControlLabel
                      key={f.src}
                      value={f.src}
                      sx={{ display: "flex", mr: 0 }}
                      control={<Radio size="small" />}
                      label={
                        <Typography variant="body2" sx={{ wordBreak: "break-all" }}>
                          {f.src}
                          {formatWeightSize(f.size)}
                        </Typography>
                      }
                    />
                  ))}
                </RadioGroup>
              ) : (
                visibleFiles.map((f) => fileRow(f))
              )}
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
            <Hint>
              {name.trim() || "Unnamed"} · {likeName || "template"} · {role}
              {bytes > 0 ? ` · ${(bytes / 1024 ** 3).toFixed(2)} GB` : ""}
              {preview.license ? ` · licence ${preview.license}` : ""}
              {preview.gated && repoUrl && (
                <>
                  {" · "}
                  <a href={repoUrl} target="_blank" rel="noreferrer noopener">
                    Agree and access
                  </a>
                </>
              )}
            </Hint>
          </>
        )}
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={onClose} disabled={saving}>
          Cancel
        </ActionButton>
        <ActionButton onClick={() => handleAdd(false)} disabled={!pending}>
          Add only
        </ActionButton>
        <ActionButton kind="primary" onClick={() => handleAdd(true)} loading={saving} disabled={!pending}>
          Add and install
        </ActionButton>
      </DialogActions>
    </Dialog>
  );
};

export default AddVideoModelDialog;
