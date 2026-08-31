// frontend/src/components/settings/ui/ActionButton.jsx
// An action. Rectangular and filled so it cannot be mistaken for a SettingChip.
// Three kinds and a link:
//   neutral      everything that runs, opens or navigates
//   primary      only while a change is pending (Set Active, Apply, Save); it
//                disappears or turns neutral once committed, so a page never
//                carries a standing primary button
//   destructive  deletes, overwrites or kills; used inside the danger zone and
//                always behind ConfirmActionDialog
//   link         navigates and changes nothing

import React from "react";
import { Button, CircularProgress, Tooltip } from "@mui/material";
import { alpha } from "@mui/material/styles";

const kindSx = (theme, kind) => {
  switch (kind) {
    case "primary":
      return {
        bgcolor: theme.palette.primary.main,
        color: theme.palette.primary.contrastText,
        "&:hover": { bgcolor: theme.palette.primary.dark },
      };
    case "destructive":
      return {
        bgcolor: alpha(theme.palette.error.main, 0.14),
        color: theme.palette.error.main,
        "&:hover": { bgcolor: alpha(theme.palette.error.main, 0.24) },
      };
    case "link":
      return {
        bgcolor: "transparent",
        color: theme.palette.primary.light,
        px: 0.75,
        minWidth: 0,
        "&:hover": { bgcolor: alpha(theme.palette.primary.main, 0.08) },
      };
    default:
      return {
        bgcolor: alpha(theme.palette.text.primary, 0.08),
        color: theme.palette.text.primary,
        "&:hover": { bgcolor: alpha(theme.palette.text.primary, 0.14) },
      };
  }
};

/**
 * @param {"neutral"|"primary"|"destructive"|"link"} [kind]
 * @param {boolean}  [loading]   swaps the start icon for a spinner and disables
 * @param {string}   [tooltip]   shown on hover; also works while disabled
 */
const ActionButton = ({ kind = "neutral", loading = false, tooltip = "", disabled, startIcon, children, sx, ...props }) => {
  const button = (
    <Button
      size="small"
      variant="text"
      disabled={disabled || loading}
      startIcon={loading ? <CircularProgress size={13} color="inherit" /> : startIcon}
      sx={(theme) => ({
        height: 30,
        px: 1.5,
        borderRadius: "6px",
        fontSize: "0.75rem",
        lineHeight: 1,
        whiteSpace: "nowrap",
        boxShadow: "none",
        "&.Mui-disabled": { opacity: 0.4, color: "inherit" },
        ...kindSx(theme, kind),
        ...(typeof sx === "function" ? sx(theme) : sx),
      })}
      {...props}
    >
      {children}
    </Button>
  );
  if (!tooltip) return button;
  return (
    <Tooltip title={tooltip}>
      <span style={{ display: "inline-flex" }}>{button}</span>
    </Tooltip>
  );
};

export default ActionButton;
