// frontend/src/components/settings/ui/DashboardStrip.jsx
// The read-only strip above the panels: what is loaded, how full the GPU is,
// what needs attention. Every tile reads an endpoint the page already polls.

import React from "react";
import { Box, LinearProgress, Paper, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";

const TONE_KEY = { ok: "success", warn: "warning", error: "error", off: null };

/**
 * @param {string} label
 * @param {ReactNode} value
 * @param {string} [sub]
 * @param {"ok"|"warn"|"error"|"off"} [tone]   dot beside the label; omit for none
 * @param {number} [progress]   0..100 draws a bar under the value
 * @param {function} [onClick]  makes the tile a link
 */
export const DashboardTile = ({ label, value, sub, tone, progress, onClick }) => (
  <Paper
    elevation={0}
    onClick={onClick}
    role={onClick ? "button" : undefined}
    tabIndex={onClick ? 0 : undefined}
    onKeyDown={onClick ? (e) => (e.key === "Enter" || e.key === " ") && onClick() : undefined}
    sx={(theme) => ({
      minWidth: 0,
      px: 1.5,
      py: 1.1,
      display: "flex",
      flexDirection: "column",
      gap: 0.25,
      border: `1px solid ${theme.palette.divider}`,
      borderRadius: "8px",
      cursor: onClick ? "pointer" : "default",
      "&:hover": onClick ? { borderColor: alpha(theme.palette.primary.main, 0.4) } : {},
    })}
  >
    <Typography
      component="span"
      sx={(theme) => ({
        display: "flex",
        alignItems: "center",
        gap: 0.75,
        fontSize: "0.66rem",
        letterSpacing: 1,
        textTransform: "uppercase",
        color: "text.secondary",
        "&::before": tone
          ? {
              content: '""',
              width: 6,
              height: 6,
              borderRadius: "50%",
              bgcolor: TONE_KEY[tone] ? theme.palette[TONE_KEY[tone]].main : alpha(theme.palette.text.primary, 0.25),
              flexShrink: 0,
            }
          : undefined,
      })}
    >
      {label}
    </Typography>
    <Typography
      component="span"
      sx={{ fontSize: "0.82rem", color: "text.primary", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
    >
      {value}
    </Typography>
    {typeof progress === "number" ? (
      <LinearProgress
        variant="determinate"
        value={Math.max(0, Math.min(100, progress))}
        color={progress > 90 ? "error" : progress > 70 ? "warning" : "success"}
        sx={{ height: 5, borderRadius: 3, mt: 0.5 }}
      />
    ) : (
      sub && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
        >
          {sub}
        </Typography>
      )
    )}
  </Paper>
);

const DashboardStrip = ({ children }) => (
  <Box
    sx={{
      display: "grid",
      gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
      gap: 1.25,
    }}
  >
    {children}
  </Box>
);

export default DashboardStrip;
