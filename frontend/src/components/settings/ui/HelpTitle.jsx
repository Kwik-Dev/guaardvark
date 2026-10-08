// frontend/src/components/settings/ui/HelpTitle.jsx
// A title that explains itself on hover. Explanations live here instead of in
// paragraphs under the title; the dotted underline is the only visible cue.
// Keyboard users reach the same text by focusing the title.

import React from "react";
import { Box, Tooltip } from "@mui/material";
import { alpha } from "@mui/material/styles";

const HelpTitle = ({ help, children }) => {
  if (!help) return children;
  return (
    <Tooltip title={help} placement="top-start" enterDelay={250} describeChild>
      <Box
        component="span"
        tabIndex={0}
        sx={(theme) => ({
          cursor: "help",
          textDecorationLine: "underline",
          textDecorationStyle: "dotted",
          textDecorationColor: alpha(theme.palette.text.secondary, 0.6),
          textUnderlineOffset: "3px",
          borderRadius: "2px",
          "&:focus-visible": { outline: `1px solid ${theme.palette.primary.main}`, outlineOffset: 2 },
        })}
      >
        {children}
      </Box>
    </Tooltip>
  );
};

export default HelpTitle;
