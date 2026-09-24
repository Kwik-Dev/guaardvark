import React from "react";
import { Snackbar, Alert as MuiAlert } from "@mui/material";

// Shared with CollapsibleAlertSnackbar, which is this component with a
// fold/hide alert body instead of a bare MUI Alert. Exported so the two cannot
// drift: a restyle here is a restyle in both.
export const alertSnackbarSx = {
  width: "100%",
  backgroundColor: "rgba(8, 10, 14, 0.85)",
  backdropFilter: "blur(12px)",
  border: "1px solid rgba(138, 155, 174, 0.25)",
  borderRadius: "8px",
  color: "rgba(255, 255, 255, 0.8)",
  fontFamily: '"Lato", sans-serif',
  '& .MuiAlert-icon': {
    color: 'inherit',
  },
  '& .MuiAlert-action': {
    color: 'rgba(255, 255, 255, 0.5)',
  },
};

// Reusable Snackbar+Alert component for displaying feedback messages
const AlertSnackbar = ({
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
    <MuiAlert
      onClose={onClose}
      severity={severity}
      elevation={6}
      variant="outlined"
      sx={alertSnackbarSx}
    >
      {message}
    </MuiAlert>
  </Snackbar>
);

export default AlertSnackbar;
