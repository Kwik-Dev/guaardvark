// frontend/src/components/FileGenPopup.jsx
// Offer to write a chat request out as a file. A plain card in the corner,
// worded as the small thing it is; "Always create" stores the tool so later
// requests of this kind start without the card (undo on the Approvals page).

import React from "react";
import { Box, Fade, Paper, Typography } from "@mui/material";
import { ActionButton } from "./settings/ui";

const KIND_LABELS = {
  codegen: "code file",
  generate_file: "file",
  generate_csv: "CSV file",
  generate_bulk_csv: "CSV file",
  generate_wordpress_content: "WordPress CSV",
  generate_enhanced_wordpress_content: "WordPress CSV",
};

const FileGenPopup = ({ open, fileData, onConfirm, onAlways, onDismiss }) => {
  if (!open || !fileData) return null;

  const { filename, isBulkRequest, quantity, toolName } = fileData;
  const kind = KIND_LABELS[toolName] || "file";
  const detail = isBulkRequest
    ? `${quantity || "Several"} rows; this can take a few minutes.`
    : null;

  return (
    <Fade in={open}>
      <Paper
        variant="outlined"
        role="dialog"
        aria-label="Create a file"
        data-testid="file-gen-card"
        sx={{
          position: "fixed",
          bottom: 24,
          right: 24,
          zIndex: 1500,
          width: 340,
          p: 1.75,
          borderRadius: 1.5,
          boxShadow: 3,
        }}
      >
        <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
          Create a {kind} from this request?
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ fontFamily: "monospace", fontSize: "0.8rem", wordBreak: "break-all" }}
        >
          {filename}
        </Typography>
        {detail && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
            {detail}
          </Typography>
        )}
        <Box sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap", mt: 1.5 }}>
          <ActionButton kind="primary" onClick={onConfirm}>
            Create
          </ActionButton>
          {onAlways && (
            <ActionButton
              kind="neutral"
              tooltip="Create files like this without asking from now on. Undo it on the Approvals page."
              onClick={onAlways}
            >
              Always create
            </ActionButton>
          )}
          <ActionButton kind="link" onClick={onDismiss}>
            Not now
          </ActionButton>
        </Box>
      </Paper>
    </Fade>
  );
};

export default FileGenPopup;
