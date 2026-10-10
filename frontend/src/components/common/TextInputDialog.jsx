// frontend/src/components/common/TextInputDialog.jsx
// The themed stand-in for window.prompt: one line of text, Enter submits, Esc or
// the backdrop cancels. The draft starts as `initialValue` (selected, as the
// browser prompt did) each time the dialog opens, and onSubmit only ever gets a
// non-empty trimmed value; cancelling never calls it.

import React, { useEffect, useState } from "react";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  TextField,
} from "@mui/material";

/**
 * @param {boolean}  open
 * @param {string}   title
 * @param {ReactNode} [description]  one line under the title
 * @param {string}   [label]         TextField label
 * @param {string}   [placeholder]
 * @param {string}   [initialValue]  prefilled and selected on open
 * @param {string}   [submitLabel]
 * @param {string}   [cancelLabel]
 * @param {string}   [inputType]     "text" | "url" | ...
 * @param {function} onSubmit        (trimmedValue) => void
 * @param {function} onClose         cancel (Esc, backdrop, Cancel button)
 */
const TextInputDialog = ({
  open,
  title,
  description,
  label,
  placeholder,
  initialValue = "",
  submitLabel = "OK",
  cancelLabel = "Cancel",
  inputType = "text",
  onSubmit,
  onClose,
}) => {
  const [value, setValue] = useState(initialValue);

  // A fresh draft every time the dialog opens; a stale one from the last use
  // must not leak into the next.
  useEffect(() => {
    if (open) setValue(initialValue ?? "");
  }, [open, initialValue]);

  const trimmed = value.trim();

  const submit = () => {
    if (!trimmed) return;
    onSubmit?.(trimmed);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: "0.9rem" }}>{title}</DialogTitle>
      <DialogContent>
        {description && (
          <DialogContentText variant="body2" sx={{ mb: 1 }}>
            {description}
          </DialogContentText>
        )}
        <TextField
          autoFocus
          fullWidth
          size="small"
          type={inputType}
          label={label}
          placeholder={placeholder}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={(e) => e.target.select()}
          inputProps={{ "aria-label": label || title }}
          sx={{ mt: 1 }}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} size="small">
          {cancelLabel}
        </Button>
        <Button onClick={submit} variant="contained" size="small" disabled={!trimmed}>
          {submitLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default TextInputDialog;
