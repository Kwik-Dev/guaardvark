// frontend/src/components/videoeditor/MediaThumb.jsx
//
// One thumbnail, one rule: it ALWAYS degrades to a kind icon — never a black
// box, never a broken-image glyph. Uses <img onError> because CSS
// background-image silently paints nothing on a 404 (that was the Bin/Folder
// black-box bug). Single source of truth for media kind icons + colors.
import React, { useState } from "react";
import { Box } from "@mui/material";
import {
  MovieFilter as VideoIcon,
  GraphicEq as AudioIcon,
  Image as ImageIcon,
} from "@mui/icons-material";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export const ICONS = { video: VideoIcon, audio: AudioIcon, image: ImageIcon };
export const ICON_COLORS = { video: "primary.main", audio: "#9c27b0", image: "info.main" };

const MediaThumb = ({
  documentId,
  thumbnailUrl,
  kind = "video",
  size = 44,
  iconSize,
  sx,
}) => {
  const Icon = ICONS[kind] || VideoIcon;
  const color = ICON_COLORS[kind] || "primary.main";
  // Prefer an explicit thumbnail_url; fall back to the on-the-fly thumbnail
  // endpoint; if neither resolves, the icon shows.
  const src = thumbnailUrl || (documentId ? `${API_BASE}/files/thumbnail?document_id=${documentId}` : null);
  const [failed, setFailed] = useState(false);

  const showImg = src && !failed;

  return (
    <Box
      sx={{
        width: size,
        height: size,
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        bgcolor: "rgba(0,0,0,0.06)",
        borderRadius: 0.5,
        overflow: "hidden",
        ...sx,
      }}
    >
      {showImg ? (
        <Box
          component="img"
          src={src}
          loading="lazy"
          onError={() => setFailed(true)}
          sx={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
        />
      ) : (
        <Icon sx={{ fontSize: iconSize || Math.round(size * 0.5), color }} />
      )}
    </Box>
  );
};

export default MediaThumb;
