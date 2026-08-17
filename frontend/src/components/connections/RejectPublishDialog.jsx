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
 * Confirm rejecting a queued publish, capturing why.
 *
 * The reason is stored on the record and is the only explanation anyone gets
 * later, so it is worth asking for even though the backend defaults it.
 */
const RejectPublishDialog = ({ open, record, busy = false, onCancel, onConfirm }) => {
  const [reason, setReason] = useState("");

  useEffect(() => {
    if (open) setReason("");
  }, [open]);

  const destination = record
    ? `${record.platform}${record.title ? ` — ${record.title}` : ""}`
    : "";

  return (
    <Dialog open={open} onClose={busy ? undefined : onCancel} maxWidth="sm" fullWidth>
      <DialogTitle>Reject this publish?</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          {destination && <strong>{destination}</strong>}
          {destination && <br />}
          It will not be sent, and it cannot be approved afterwards — a new
          publish has to be queued instead. The reason below is kept on the
          record.
        </DialogContentText>
        <TextField
          autoFocus
          fullWidth
          multiline
          minRows={2}
          label="Reason (optional)"
          placeholder="Why is this not going out?"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          disabled={busy}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} disabled={busy}>
          Keep it
        </Button>
        <Button
          onClick={() => onConfirm(reason.trim())}
          color="error"
          variant="contained"
          disabled={busy}
        >
          {busy ? "Rejecting…" : "Reject"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default RejectPublishDialog;
