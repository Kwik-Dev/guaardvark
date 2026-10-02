// The imported reference clips, with the two ways to take one back:
//   Withdraw consent  removes the clip's consent record. The clip stays and is
//                     not cloned until consent is confirmed again (picking it
//                     in the Studio asks). Reversible, so a plain confirmation.
//   Delete clip       removes the clip and its record. Irreversible, so the
//                     Settings kit's ConfirmActionDialog. On another device it
//                     needs this install's API key (auth_guard).
// Consent is checked when a clone starts and the clip is read once then
// (plugins/audio_foundry/backends/voice_gen_chatterbox.py), so a clone already
// running finishes and one still waiting to start is refused.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
} from "@mui/material";

import { deleteVoiceClip, withdrawVoiceClipConsent } from "../../api/audioFoundryService";
import { formatUiError } from "../../utils/uiError";
import { ApiKeyRefusalAlert } from "../common/ApiKeyRefusalNotice";
import { ActionButton, ConfirmActionDialog, StatusPill } from "../settings/ui";

const sizeLabel = (bytes) => `${Math.max(1, Math.round((bytes || 0) / 1024))} KB`;

const failure = (err, fallback) => ({
  message:
    (err?.authRefused && err.message) ||
    formatUiError(err?.response?.data?.error) ||
    err?.message ||
    fallback,
  authRefused: Boolean(err?.authRefused),
});

const VoiceClipManager = ({ clips, onChanged, onRemoved, defaultOpen = false }) => {
  const [open, setOpen] = useState(defaultOpen);
  const [withdrawing, setWithdrawing] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  if (!clips.length) return null;

  const run = async (action, clip, fallback, removed) => {
    setBusy(true);
    setError(null);
    try {
      await action(clip);
      onRemoved?.(clip, removed);
      onChanged?.();
      setWithdrawing(null);
      setDeleting(null);
    } catch (err) {
      setError(failure(err, fallback));
      setWithdrawing(null);
      setDeleting(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box>
      <ActionButton kind="link" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {open ? "Hide imported clips" : `Manage imported clips (${clips.length})`}
      </ActionButton>
      <Collapse in={open} unmountOnExit>
        <Stack spacing={0.75} sx={{ mt: 1 }}>
          {error &&
            (error.authRefused ? (
              <ApiKeyRefusalAlert message={error.message} />
            ) : (
              <Alert severity="error" onClose={() => setError(null)}>
                {error.message}
              </Alert>
            ))}
          {clips.map((clip) => (
            <Box
              key={clip.filename || clip.id}
              data-testid="voice-clip-row"
              sx={{
                display: "flex",
                flexWrap: "wrap",
                alignItems: "center",
                gap: 1,
                px: 1.25,
                py: 0.75,
                borderRadius: "6px",
                border: 1,
                borderColor: "divider",
              }}
            >
              <Box sx={{ flex: "1 1 160px", minWidth: 0 }}>
                <Typography variant="body2" noWrap title={clip.filename}>
                  {clip.filename}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {sizeLabel(clip.size_bytes)}
                </Typography>
              </Box>
              <StatusPill
                label={clip.consented ? "Consent recorded" : "No consent"}
                tone={clip.consented ? "ok" : "neutral"}
                tooltip={
                  clip.consented
                    ? "Can be used to clone a voice."
                    : "Cannot be used to clone a voice until consent is confirmed; the Studio asks when you pick it."
                }
              />
              {clip.consented && (
                <ActionButton onClick={() => setWithdrawing(clip)} disabled={busy}>
                  Withdraw consent
                </ActionButton>
              )}
              <ActionButton kind="destructive" onClick={() => setDeleting(clip)} disabled={busy}>
                Delete clip
              </ActionButton>
            </Box>
          ))}
        </Stack>
      </Collapse>

      <Dialog
        open={Boolean(withdrawing)}
        onClose={busy ? undefined : () => setWithdrawing(null)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle sx={{ fontSize: "1rem" }}>Withdraw consent for this clip?</DialogTitle>
        <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
          <Typography variant="body2" fontWeight="bold" sx={{ wordBreak: "break-word" }}>
            {withdrawing?.filename}
          </Typography>
          <Typography variant="body2">
            The clip stays in your library, but it cannot be used to clone a voice until you confirm
            consent again.
          </Typography>
          <Typography variant="caption" color="text.secondary">
            A voiceover already being cloned from it finishes; one still waiting to start is refused.
          </Typography>
        </DialogContent>
        <DialogActions>
          <ActionButton onClick={() => setWithdrawing(null)} disabled={busy}>
            Cancel
          </ActionButton>
          <ActionButton
            kind="primary"
            loading={busy}
            onClick={() => run(withdrawVoiceClipConsent, withdrawing, "Consent could not be withdrawn.", "withdrawn")}
          >
            Withdraw consent
          </ActionButton>
        </DialogActions>
      </Dialog>

      <ConfirmActionDialog
        open={Boolean(deleting)}
        title="Delete this voice clip?"
        description={
          "Removes the recording and its consent record from this machine, and from the Video page's " +
          "audio guide list. A voiceover already being cloned from it finishes; one still waiting to start fails."
        }
        facts={
          deleting
            ? [
                { label: "Clip", value: deleting.filename },
                { label: "Size", value: sizeLabel(deleting.size_bytes) },
                { label: "Consent", value: deleting.consented ? "recorded" : "none" },
              ]
            : []
        }
        keeps="voiceovers already made from it, which stay in your Audio library."
        confirmLabel="Delete clip"
        busy={busy}
        onConfirm={() => run(deleteVoiceClip, deleting, "The clip could not be deleted.", "deleted")}
        onClose={() => setDeleting(null)}
      />
    </Box>
  );
};

VoiceClipManager.propTypes = {
  clips: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      filename: PropTypes.string,
      size_bytes: PropTypes.number,
      consented: PropTypes.bool,
    }),
  ).isRequired,
  onChanged: PropTypes.func,
  onRemoved: PropTypes.func,
  defaultOpen: PropTypes.bool,
};

export default VoiceClipManager;
