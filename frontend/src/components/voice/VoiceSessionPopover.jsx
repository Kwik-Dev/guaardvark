import React from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Divider,
  FormControlLabel,
  LinearProgress,
  Link,
  MenuItem,
  Popover,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { useShallow } from "zustand/react/shallow";
import { useVoice } from "../../contexts/VoiceContext";
import { useVoiceSession, useVoiceSessionState } from "../../contexts/VoiceSessionContext";
import { useAppStore } from "../../stores/useAppStore";
import { useVoiceSettings } from "../../hooks/useVoiceSettings";
import {
  ACTIVATION_MODES,
  ACTIVATION_MODE_HELP,
  ACTIVATION_MODE_LABELS,
  updateVoiceSettings,
} from "../../config/voiceDefaults";
import { describeVoicePhase, OUTCOME_LABELS } from "../../utils/voicePhase";
import brand from "../../config/brand";

// The worklet's RMS for ordinary speech sits well under 0.3; scale it to the bar.
const LEVEL_SCALE = 300;

/**
 * Status and quick settings for the global mic: what it is doing, the last
 * thing it heard, start/stop, mode, wake phrase and reply voice.
 */
const PLACEMENTS = {
  below: {
    anchorOrigin: { vertical: "bottom", horizontal: "right" },
    transformOrigin: { vertical: "top", horizontal: "right" },
  },
  // Beside a sidebar button, opening into the page.
  right: {
    anchorOrigin: { vertical: "bottom", horizontal: "right" },
    transformOrigin: { vertical: "bottom", horizontal: "left" },
  },
};

const VoiceSessionPopover = ({ anchorEl, open, onClose, placement = "below" }) => {
  const voice = useVoiceSession();
  const state = useVoiceSessionState(
    useShallow((s) => ({
      phase: s.phase,
      session: s.session,
      listenMode: s.listenMode,
      push: s.push,
      error: s.error,
      errorCode: s.errorCode,
      notice: s.notice,
      lastTranscript: s.lastTranscript,
      speaking: s.speaking,
    }))
  );
  const level = useVoiceSessionState((s) => s.level);
  const mode = useAppStore((s) => s.voiceActivationMode);
  const setMode = useAppStore((s) => s.setVoiceActivationMode);
  const systemName = useAppStore((s) => s.systemName) || brand.appName;
  const settings = useVoiceSettings();
  const { availableVoices = [] } = useVoice() || {};
  const installedVoices = availableVoices.filter((v) => v.available !== false);
  const { title, hint } = describeVoicePhase(state, { systemName, mode });
  const running = Boolean(state.session);
  const fixInSettings = state.errorCode === "model" || state.errorCode === "mic-off";

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      {...(PLACEMENTS[placement] || PLACEMENTS.below)}
      // Above the floating chat card (zIndex 1400), which it often overlaps.
      sx={{ zIndex: 1500 }}
      slotProps={{ paper: { sx: { width: 320, p: 2 } } }}
    >
      <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
        {title}
      </Typography>
      {hint && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
          {hint}
        </Typography>
      )}
      <LinearProgress
        variant="determinate"
        value={Math.min(100, level * LEVEL_SCALE)}
        aria-label="Microphone level"
        sx={{ my: 1.5, height: 6, borderRadius: 1 }}
      />

      {state.error && (
        <Alert
          severity={state.errorCode === "denied" ? "warning" : "error"}
          sx={{ mb: 1.5, py: 0 }}
          onClose={() => voice.clearError()}
          action={
            fixInSettings ? (
              <Button component={RouterLink} to="/settings#settings-voice" size="small" color="inherit" onClick={onClose}>
                Settings
              </Button>
            ) : undefined
          }
        >
          {state.error}
        </Alert>
      )}
      {state.notice && !state.error && (
        <Typography variant="caption" color="warning.main" sx={{ display: "block", mb: 1 }}>
          {state.notice.message}
        </Typography>
      )}

      {state.lastTranscript && (
        <Box sx={{ mb: 1.5 }}>
          <Typography variant="caption" color="text.secondary">
            Last heard ({OUTCOME_LABELS[state.lastTranscript.outcome] || state.lastTranscript.outcome})
          </Typography>
          <Typography variant="body2" sx={{ fontStyle: "italic", wordBreak: "break-word" }}>
            “{state.lastTranscript.text}”
          </Typography>
        </Box>
      )}

      <Box sx={{ display: "flex", gap: 1, mb: 1.5 }}>
        {running ? (
          <Button variant="contained" color="error" size="small" onClick={() => voice.stop()}>
            Stop listening
          </Button>
        ) : (
          <Button
            variant="contained"
            size="small"
            onClick={() => voice.start(mode === "handsfree" ? "handsfree" : "utterance")}
          >
            {mode === "handsfree" ? "Start listening" : "Talk now"}
          </Button>
        )}
        {state.speaking && (
          <Button variant="outlined" size="small" onClick={() => voice.stopSpeaking()}>
            Stop speaking
          </Button>
        )}
      </Box>

      <Divider sx={{ mb: 1.5 }} />

      <Typography variant="caption" color="text.secondary">
        Mic button
      </Typography>
      <ToggleButtonGroup
        exclusive
        size="small"
        fullWidth
        value={mode}
        onChange={(_e, next) => next && setMode(next)}
        sx={{ mt: 0.5 }}
      >
        {ACTIVATION_MODES.map((m) => (
          <ToggleButton key={m} value={m} sx={{ textTransform: "none", fontSize: "0.75rem", py: 0.25 }}>
            {ACTIVATION_MODE_LABELS[m]}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
        {ACTIVATION_MODE_HELP[mode]}
      </Typography>

      <FormControlLabel
        sx={{ mt: 0.5 }}
        control={
          <Switch
            size="small"
            checked={settings.wakeWordEnabled}
            disabled={mode !== "handsfree"}
            onChange={(e) => updateVoiceSettings({ wakeWordEnabled: e.target.checked })}
          />
        }
        label={<Typography variant="body2">Wait for “Hey {systemName}”</Typography>}
      />
      <FormControlLabel
        control={
          <Switch
            size="small"
            checked={settings.ttsEnabled}
            onChange={(e) => updateVoiceSettings({ ttsEnabled: e.target.checked })}
          />
        }
        label={<Typography variant="body2">Speak replies</Typography>}
      />
      {installedVoices.length > 0 && (
        <TextField
          select
          size="small"
          fullWidth
          label="Reply voice"
          value={installedVoices.some((v) => v.id === settings.voice) ? settings.voice : ""}
          onChange={(e) => updateVoiceSettings({ voice: e.target.value })}
          disabled={!settings.ttsEnabled}
          sx={{ mt: 1 }}
          SelectProps={{ MenuProps: { sx: { zIndex: 1600 } } }}
        >
          {installedVoices.map((v) => (
            <MenuItem key={v.id} value={v.id}>
              {v.name || v.id}
            </MenuItem>
          ))}
        </TextField>
      )}

      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mt: 1.5 }}>
        <Typography variant="caption" color="text.secondary">
          Ctrl+Shift+Space: tap to start or stop, hold to talk
        </Typography>
      </Box>
      <Box sx={{ display: "flex", gap: 2, mt: 0.5 }}>
        <Link component={RouterLink} to="/voice-chat" variant="body2" onClick={onClose}>
          Voice page
        </Link>
        <Link component={RouterLink} to="/settings#settings-voice" variant="body2" onClick={onClose}>
          Voice settings
        </Link>
      </Box>
    </Popover>
  );
};

VoiceSessionPopover.propTypes = {
  anchorEl: PropTypes.object,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  placement: PropTypes.oneOf(["below", "right"]),
};

export default VoiceSessionPopover;
