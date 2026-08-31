// frontend/src/components/settings/ui/SettingsCluster.jsx
// The unit of layout inside a SettingsPanel: a small uppercase label, then one
// wrapping line of chips, fields and buttons. Nothing here puts a label on the
// left and a lone control on the right.

import React from "react";
import { Box, Typography } from "@mui/material";

export const Cluster = ({ label, note, children, sx }) => (
  <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75, minWidth: 0, ...sx }}>
    {label && (
      <Box sx={{ display: "flex", alignItems: "baseline", gap: 1 }}>
        <Typography
          component="span"
          sx={{ fontSize: "0.66rem", letterSpacing: 1, textTransform: "uppercase", color: "text.secondary", opacity: 0.85 }}
        >
          {label}
        </Typography>
        {note && (
          <Typography component="span" variant="caption" sx={{ color: "text.secondary", opacity: 0.7 }}>
            {note}
          </Typography>
        )}
      </Box>
    )}
    {children}
  </Box>
);

/** A wrapping line of controls. `nowrap` keeps a field and its button together. */
export const Line = ({ children, nowrap = false, sx }) => (
  <Box
    sx={{
      display: "flex",
      alignItems: "center",
      flexWrap: nowrap ? "nowrap" : "wrap",
      gap: 0.75,
      minWidth: 0,
      "& > .grow": { flex: 1, minWidth: 140 },
      ...sx,
    }}
  >
    {children}
  </Box>
);

/** A 1px divider between groups on the same line. */
export const Sep = () => (
  <Box component="span" aria-hidden sx={{ width: "1px", height: 18, bgcolor: "divider", mx: 0.5, flexShrink: 0 }} />
);

/** Quiet explanatory text on a line. */
export const Hint = ({ children, sx }) => (
  <Typography variant="caption" sx={{ color: "text.secondary", lineHeight: 1.4, ...sx }}>
    {children}
  </Typography>
);
