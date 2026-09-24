import React from "react";
import { Snackbar } from "@mui/material";
import CollapsibleAlert from "./CollapsibleAlert";
import { alertSnackbarSx } from "./AlertSnackbar";

// The fold/hide twin of AlertSnackbar: same props, same look, but the alert
// body can be collapsed by clicking the toggle icon (CollapsibleAlert), which
// matters for the long JSON / multi-line status messages some pages push
// through a snackbar.
//
// Drop-in for AlertSnackbar — open, onClose, severity, message,
// autoHideDuration, anchorOrigin, plus any Snackbar prop via ...props.
const CollapsibleAlertSnackbar = ({
  open,
  onClose,
  severity = "info",
  message,
  autoHideDuration = 4000,
  anchorOrigin = { vertical: "bottom", horizontal: "center" },
  ...props
}) => (
  <Snackbar
    open={open}
    autoHideDuration={autoHideDuration}
    onClose={onClose}
    anchorOrigin={anchorOrigin}
    {...props}
  >
    <CollapsibleAlert
      onClose={onClose}
      severity={severity}
      elevation={6}
      variant="outlined"
      sx={alertSnackbarSx}
    >
      {message}
    </CollapsibleAlert>
  </Snackbar>
);

export default CollapsibleAlertSnackbar;
