// frontend/src/components/settings/SettingsSection.jsx
// Reusable section wrapper for SettingsPage - Cursor/VS Code style

import React from "react";
import { Box, Typography } from "@mui/material";

// `icon` is consumed here rather than spread onto the Box: an unknown prop
// carrying a React element reaches the DOM node otherwise. Callers that pass
// no title (a section rendered under a page header that already names it)
// get the body alone.
const SettingsSection = ({ title, icon, children, ...props }) => {
  const showHeader = Boolean(title || icon);
  return (
    <Box {...props}>
      {showHeader && (
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, mb: 1.5 }}>
          {icon && (
            <Box
              sx={{
                display: "flex",
                color: "text.secondary",
                "& .MuiSvgIcon-root": { fontSize: "1.1rem" },
              }}
            >
              {icon}
            </Box>
          )}
          <Typography
            variant="overline"
            sx={{
              fontSize: "0.75rem",
              letterSpacing: 1,
              color: "text.secondary",
              display: "block",
            }}
          >
            {title}
          </Typography>
        </Box>
      )}
      <Box sx={{ display: "flex", flexDirection: "column", gap: 0 }}>
        {children}
      </Box>
    </Box>
  );
};

export default SettingsSection;
