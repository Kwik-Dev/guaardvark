// Asks the person to confirm they may clone a voice before a reference clip
// is imported for cloning (or before an older clip is used). The same step as
// the chat's likeness consent card: nothing is recorded unless they confirm,
// and the backend refuses to clone a clip without the record.
import React from "react";
import PropTypes from "prop-types";
import {
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from "@mui/material";

// Shown when GET /voice-clips has not supplied the backend's own wording.
export const FALLBACK_VOICE_CONSENT_STATEMENT =
  "I have the right to clone this voice: it is my own, or the person speaking agreed to have their voice cloned.";

const VoiceConsentDialog = ({ open, clipName, statement, busy, onConfirm, onCancel }) => (
  <Dialog open={open} onClose={busy ? undefined : onCancel} maxWidth="xs" fullWidth>
    <DialogTitle>Clone this voice?</DialogTitle>
    <DialogContent>
      {clipName && (
        <Typography variant="body2" fontWeight="bold" sx={{ mb: 1.5, wordBreak: "break-word" }}>
          {clipName}
        </Typography>
      )}
      <Typography variant="body2" sx={{ mb: 1.5, lineHeight: 1.5 }}>
        By confirming, you state: &ldquo;{statement || FALLBACK_VOICE_CONSENT_STATEMENT}&rdquo;
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
        Your confirmation is saved with the clip. A clip without it cannot be used to clone a voice.
      </Typography>
    </DialogContent>
    <DialogActions>
      <Button onClick={onCancel} disabled={busy}>Cancel</Button>
      <Button
        variant="contained"
        onClick={onConfirm}
        disabled={busy}
        startIcon={busy ? <CircularProgress size={16} color="inherit" /> : null}
      >
        I confirm
      </Button>
    </DialogActions>
  </Dialog>
);

VoiceConsentDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  clipName: PropTypes.string,
  statement: PropTypes.string,
  busy: PropTypes.bool,
  onConfirm: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

export default VoiceConsentDialog;
