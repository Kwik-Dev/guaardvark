// Ordered list of "clip A 0:00-0:03 [filter] → transition → clip B ...".
// Renders the Plan pipeline's arrangement.clips. Read-only in A1; A3 adds
// drag-to-reorder + per-cut transition swap. Per-clip caption position/style
// can be edited here (onUpdateCaption(index, patch)).

import React from "react";
import { Box, Stack, Chip, Typography, Select, MenuItem, TextField, FormControl, InputLabel } from "@mui/material";

const fmtTime = (s) => {
  const m = Math.floor(s / 60);
  const r = (s - m * 60).toFixed(1);
  return `${m}:${r.padStart(4, "0")}`;
};

const ALIGNMENTS = [
  ["left", "top", "Top-left"],
  ["center", "top", "Top-center"],
  ["right", "top", "Top-right"],
  ["left", "center", "Center-left"],
  ["center", "center", "Center"],
  ["right", "center", "Center-right"],
  ["left", "bottom", "Bottom-left"],
  ["center", "bottom", "Bottom-center"],
  ["right", "bottom", "Bottom-right"],
];

const CaptionEditor = ({ clip, index, onUpdateCaption }) => {
  const [open, setOpen] = React.useState(false);
  const set = (patch) => onUpdateCaption?.(index, patch);
  const alignKey = `${clip.caption_halign || "center"}|${clip.caption_valign || "bottom"}`;
  return (
    <Box sx={{ pl: 1, mt: 0.5 }}>
      <Typography
        variant="caption"
        sx={{ color: "primary.main", cursor: "pointer", textDecoration: "underline" }}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "▾" : "▸"} Caption: “{clip.caption}”
      </Typography>
      {open && (
        <Stack spacing={1} sx={{ mt: 0.5 }}>
          <TextField
            size="small"
            label="Caption text"
            multiline
            minRows={1}
            value={clip.caption || ""}
            onChange={(e) => set({ caption: e.target.value })}
          />
          <FormControl size="small" fullWidth>
            <InputLabel>Position</InputLabel>
            <Select
              value={alignKey}
              label="Position"
              onChange={(e) => {
                const [halign, valign] = e.target.value.split("|");
                set({ caption_halign: halign, caption_valign: valign });
              }}
            >
              {ALIGNMENTS.map(([h, v, label]) => (
                <MenuItem key={`${h}|${v}`} value={`${h}|${v}`}>{label}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <Stack direction="row" spacing={1}>
            <TextField
              size="small" label="Size" type="number" value={clip.caption_size ?? 48}
              onChange={(e) => set({ caption_size: Number(e.target.value) || 48 })}
            />
            <TextField
              size="small" label="Color" value={clip.caption_color || "#ffffff"}
              onChange={(e) => set({ caption_color: e.target.value })}
            />
            <TextField
              size="small" label="Background" value={clip.caption_bgcolor || "#00000000"}
              onChange={(e) => set({ caption_bgcolor: e.target.value })}
            />
          </Stack>
        </Stack>
      )}
    </Box>
  );
};

const ArrangementPreview = ({ arrangement, onUpdateCaption }) => {
  if (!arrangement || !arrangement.clips || arrangement.clips.length === 0) {
    return (
      <Typography variant="caption" color="text.secondary">
        Hit Plan to generate an arrangement.
      </Typography>
    );
  }

  return (
    <Stack spacing={0.5}>
      <Typography variant="caption" color="text.secondary">
        Arrangement · style: {arrangement.style_recipe_name} · seed: {arrangement.seed}
      </Typography>
      {arrangement.clips.map((c, i) => (
        <Box
          key={`${c.clip_id}-${i}`}
          sx={{
            p: 0.5,
            borderRadius: 0.5,
            border: 1,
            borderColor: "divider",
            backgroundColor: "background.paper",
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Chip size="small" label={c.section_label} sx={{ minWidth: 60 }} />
            <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
              {fmtTime(c.timeline_start)}-{fmtTime(c.timeline_end)}
            </Typography>
            <Typography variant="caption" sx={{ flexGrow: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {c.clip_id}
            </Typography>
            {c.filter_preset && c.filter_preset !== "none" && (
              <Chip size="small" label={c.filter_preset} color="primary" variant="outlined" />
            )}
            {i < arrangement.clips.length - 1 && c.transition_to_next !== "hard-cut" && (
              <Chip size="small" label={`→ ${c.transition_to_next}`} variant="outlined" />
            )}
          </Box>
          {c.caption && <CaptionEditor clip={c} index={i} onUpdateCaption={onUpdateCaption} />}
        </Box>
      ))}
    </Stack>
  );
};

export default ArrangementPreview;
