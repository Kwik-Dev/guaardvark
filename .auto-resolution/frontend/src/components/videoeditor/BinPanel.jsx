// Project Bin — multi-clip pool for the auto-edit. Accepts drops from
// MediaLibraryPanel (videos) and from the OS file browser. Drag-out is not
// supported; the bin owns its clips. Remove via the X on each tile.

import React, { useRef } from "react";
import { Box, Stack, Typography, LinearProgress, Alert, IconButton, Tooltip } from "@mui/material";
import {
  VideoLibrary as VideoIcon,
  FolderOpen as OpenFolderIcon,
  ArrowUpward as ArrowUpIcon,
  ArrowDownward as ArrowDownIcon,
} from "@mui/icons-material";
import BinClipTile from "./BinClipTile";
import { useExternalDrop } from "./useExternalDrop";

const BinPanel = ({
  binClips,
  selectedClipId,
  onSelect,
  onAdd,        // (BinClip) => void   — single clip add (from library drag)
  onAddMany,    // (BinClip[]) => void — bulk add (from OS upload)
  onRemove,
  onReorder,    // (fromClipId, toClipId) => void — drag-to-reorder
  onMove,       // (clipId, dir) => void — up/down arrow reorder (header bar)
  warningsByClipId = {},  // {clipId: warning text}
  planDecorationsByClipId = {},
}) => {
  // Ref (not state) holds the drag source id so dragging never re-renders the
  // source tile (which would cancel the HTML5 drag before the drop fires).
  const draggingIdRef = useRef(null);

  // OS file drop: upload → Document → bin tile.
  const { onDrop, onDragOver, uploading, progress, error } = useExternalDrop({
    onUploaded: (docs) => {
      const newClips = docs.map((d) => ({
        clipId: `doc${d.id}`,
        documentId: d.id,
        filename: d.filename || d.name || "(unnamed)",
        kind: d.kind || "video",
        keptRanges: null,
        durationSeconds: null,
      }));
      onAddMany(newClips);
    },
  });

  // MediaLibrary drag drop: dataTransfer carries { id, kind, filename }.
  const handleLibraryDrop = (event) => {
    try {
      const raw = event.dataTransfer.getData("application/json");
      if (!raw) {
        // Maybe it's an OS file drop — let useExternalDrop handle it
        onDrop(event);
        return;
      }
      const data = JSON.parse(raw);
      event.preventDefault();
      onAdd({
        clipId: `doc${data.id}`,
        documentId: data.id,
        filename: data.filename,
        kind: data.kind || "video",
        keptRanges: null,
        durationSeconds: null,
      });
    } catch {
      // Not JSON — treat as OS file drop
      onDrop(event);
    }
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1, py: 0.75, borderBottom: 1, borderColor: "divider" }}>
        <VideoIcon fontSize="small" />
        <Typography variant="subtitle2" sx={{ flexGrow: 1 }}>Project Bin</Typography>
        <Tooltip title="Move selected clip up">
          <span>
            <IconButton
              size="small"
              disabled={!selectedClipId || binClips.findIndex((c) => c.clipId === selectedClipId) <= 0}
              onClick={() => onMove?.(selectedClipId, -1)}
            >
              <ArrowUpIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip title="Move selected clip down">
          <span>
            <IconButton
              size="small"
              disabled={!selectedClipId || binClips.findIndex((c) => c.clipId === selectedClipId) >= binClips.length - 1}
              onClick={() => onMove?.(selectedClipId, 1)}
            >
              <ArrowDownIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        <Typography variant="caption" color="text.secondary">{binClips.length} clip{binClips.length !== 1 ? "s" : ""}</Typography>
      </Stack>

      <Box
        onDrop={handleLibraryDrop}
        onDragOver={onDragOver}
        sx={{
          flexGrow: 1,
          overflow: "auto",
          p: 1,
          backgroundColor: "background.default",
          border: 2,
          borderColor: "transparent",
          borderStyle: "dashed",
          "&.drag-over": { borderColor: "primary.main" },
        }}
      >
        {uploading && (
          <Box sx={{ mb: 1 }}>
            <Typography variant="caption">Uploading...</Typography>
            <LinearProgress variant="determinate" value={progress} />
          </Box>
        )}
        {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
        {binClips.length === 0 ? (
          <Stack alignItems="center" justifyContent="center" sx={{ height: "100%", color: "text.secondary" }}>
            <OpenFolderIcon sx={{ fontSize: 40, opacity: 0.4 }} />
            <Typography variant="caption">Drag clips from the Library or your file browser</Typography>
          </Stack>
        ) : (
          <Stack spacing={1}>
            {binClips.map((c) => (
              <BinClipTile
                key={c.clipId}
                clip={c}
                selected={selectedClipId === c.clipId}
                onSelect={onSelect}
                onRemove={onRemove}
                onReorder={onReorder}
                draggingIdRef={draggingIdRef}
                warning={warningsByClipId[c.clipId]}
                keptRanges={planDecorationsByClipId[c.clipId]?.keptRanges}
                durationSeconds={planDecorationsByClipId[c.clipId]?.durationSeconds}
              />
            ))}
          </Stack>
        )}
      </Box>
    </Box>
  );
};

export default BinPanel;
