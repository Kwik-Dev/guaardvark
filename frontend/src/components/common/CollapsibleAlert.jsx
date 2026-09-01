// CollapsibleAlert — a drop-in wrapper around MUI <Alert> that lets the user
// fold/hide the message body by clicking a toggle icon in the alert's action
// area. Clicking the icon again expands the message back. Useful for long
// status/warning messages in panels that would otherwise take up a lot of space.
//
// Usage is identical to <Alert>:
//   <CollapsibleAlert severity="warning" sx={{ mb: 1 }}>Some message</CollapsibleAlert>
// Any existing `action` / `onClose` props are preserved alongside the toggle.

import React, { useState } from "react";
import { Alert, IconButton, Collapse, Box } from "@mui/material";
import { KeyboardArrowDown, KeyboardArrowUp } from "@mui/icons-material";

const CollapsibleAlert = ({ children, action, onClose, ...alertProps }) => {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <Alert
      {...alertProps}
      onClose={onClose}
      action={
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          {action}
          <IconButton
            size="small"
            onClick={() => setCollapsed((c) => !c)}
            aria-label={collapsed ? "Show message" : "Hide message"}
            title={collapsed ? "Show message" : "Hide message"}
            sx={{ p: 0.25 }}
          >
            {collapsed ? <KeyboardArrowUp /> : <KeyboardArrowDown />}
          </IconButton>
        </Box>
      }
    >
      <Collapse in={!collapsed} unmountOnExit>
        <Box>{children}</Box>
      </Collapse>
    </Alert>
  );
};

export default CollapsibleAlert;
