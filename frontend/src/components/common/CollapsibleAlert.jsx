// CollapsibleAlert — a drop-in wrapper around MUI <Alert> that lets the user
// fold/hide the message body by clicking a toggle icon in the alert's action
// area. Clicking the icon again expands the message back. Useful for long
// status/warning messages in panels that would otherwise take up a lot of space.
//
// Usage is identical to <Alert>:
//   <CollapsibleAlert severity="warning" sx={{ mb: 1 }}>Some message</CollapsibleAlert>
// Any existing `action` / `onClose` props are preserved alongside the toggle.

import React, { forwardRef, useState } from "react";
import { Alert, IconButton, Collapse, Box } from "@mui/material";
import { KeyboardArrowDown, KeyboardArrowUp } from "@mui/icons-material";

// forwardRef is REQUIRED: this component is used as the child of MUI <Grow>
// (e.g. the Snackbar's default transition). Grow reads the child's DOM node via
// its ref (reflow = node => node.scrollTop); without forwarding the ref the node
// is null and Grow throws "Cannot read properties of null (reading 'scrollTop')".
const CollapsibleAlert = forwardRef(function CollapsibleAlert(
  { children, action, onClose, ...alertProps },
  ref
) {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <Alert
      ref={ref}
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
      {/* NOTE: no unmountOnExit — when this Collapse sits inside a MUI <Grow>
          (e.g. the Snackbar), unmountOnExit makes the Collapse read scrollTop of
          a null ref during the transition and throw. Keep the child mounted and
          let the Collapse hide it. */}
      <Collapse in={!collapsed}>
        <Box>{children}</Box>
      </Collapse>
    </Alert>
  );
});

export default CollapsibleAlert;
