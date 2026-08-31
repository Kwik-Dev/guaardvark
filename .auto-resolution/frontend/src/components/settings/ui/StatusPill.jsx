// frontend/src/components/settings/ui/StatusPill.jsx
// Read-only status. No outline and a coloured dot, so it never looks like a
// SettingChip (which is pressable) or an ActionButton.

import React from "react";
import { Box, Tooltip } from "@mui/material";
import { alpha } from "@mui/material/styles";

const TONES = {
  ok: "success",
  warn: "warning",
  error: "error",
  info: "info",
  neutral: null,
};

/**
 * @param {string} label
 * @param {"ok"|"warn"|"error"|"info"|"neutral"} [tone]
 * @param {string} [tooltip]
 */
const StatusPill = ({ label, tone = "neutral", tooltip = "", sx }) => {
  const pill = (
    <Box
      component="span"
      sx={(theme) => {
        const key = TONES[tone];
        const color = key ? theme.palette[key].main : theme.palette.text.secondary;
        return {
          display: "inline-flex",
          alignItems: "center",
          gap: 0.75,
          height: 22,
          px: 1,
          borderRadius: "11px",
          bgcolor: alpha(color, key ? 0.14 : 0.08),
          color,
          fontSize: "0.7rem",
          fontWeight: 500,
          whiteSpace: "nowrap",
          lineHeight: 1,
          "&::before": {
            content: '""',
            width: 6,
            height: 6,
            borderRadius: "50%",
            bgcolor: color,
            flexShrink: 0,
          },
          ...sx,
        };
      }}
    >
      {label}
    </Box>
  );
  return tooltip ? <Tooltip title={tooltip}>{pill}</Tooltip> : pill;
};

export default StatusPill;
