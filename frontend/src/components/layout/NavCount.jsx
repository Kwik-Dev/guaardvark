// A live count beside a navigation label (approvals waiting, fixes waiting).
// It follows the label instead of riding it as a badge, which covered the last
// letters of a workspace name and the icon of a tool.
import React from "react";
import { Box } from "@mui/material";

const NavCount = ({ count }) => (
  <Box
    component="span"
    sx={{
      ml: 0.75,
      minWidth: 16,
      height: 16,
      px: 0.5,
      borderRadius: "8px",
      bgcolor: "warning.main",
      color: "warning.contrastText",
      fontSize: "0.6rem",
      fontWeight: 700,
      lineHeight: "16px",
      textAlign: "center",
      letterSpacing: 0,
      flexShrink: 0,
    }}
  >
    {count > 99 ? "99+" : count}
  </Box>
);

export default NavCount;
