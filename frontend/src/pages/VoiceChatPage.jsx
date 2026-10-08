import React, { useCallback, useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  List,
  ListItem,
  ListItemText,
  Paper,
  Typography,
} from "@mui/material";
import MicNoneIcon from "@mui/icons-material/MicNone";
import RefreshIcon from "@mui/icons-material/Refresh";
import { useShallow } from "zustand/react/shallow";
import voiceService from "../api/voiceService";
import GlobalMicButton from "../components/voice/GlobalMicButton";
import VoiceListeningSettings from "../components/voice/VoiceListeningSettings";
import { useVoiceSession, useVoiceSessionState } from "../contexts/VoiceSessionContext";
import { useAppStore } from "../stores/useAppStore";
import brand from "../config/brand";
import { describeVoicePhase, OUTCOME_LABELS } from "../utils/voicePhase";

/** Microphone permission as the browser reports it, without asking for it. */
function useMicPermission() {
  const [permission, setPermission] = useState("unknown");
  useEffect(() => {
    if (typeof window !== "undefined" && window.isSecureContext === false) {
      setPermission("insecure");
      return undefined;
    }
    let status = null;
    let cancelled = false;
    const update = () => !cancelled && status && setPermission(status.state);
    navigator.permissions
      ?.query?.({ name: "microphone" })
      .then((result) => {
        status = result;
        update();
        status.onchange = update;
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      if (status) status.onchange = null;
    };
  }, []);
  return permission;
}

const PERMISSION_TEXT = {
  granted: ["Allowed", "success"],
  prompt: ["Asked on first use", "default"],
  denied: ["Blocked in the browser", "error"],
  insecure: ["Needs HTTPS or localhost", "error"],
  unknown: ["Asked on first use", "default"],
};

const StatusRow = ({ label, value, tone = "default", detail }) => (
  <Box sx={{ display: "flex", alignItems: "baseline", gap: 1.5, py: 0.75, flexWrap: "wrap" }}>
    <Typography variant="body2" sx={{ minWidth: 160, color: "text.secondary" }}>
      {label}
    </Typography>
    <Chip size="small" label={value} color={tone} variant={tone === "default" ? "outlined" : "filled"} />
    {detail && (
      <Typography variant="caption" color="text.secondary">
        {detail}
      </Typography>
    )}
  </Box>
);

/**
 * The Voice page: whether voice works on this machine and how the global mic
 * listens. Talking happens through the mic in the top bar (or sidebar), which
 * sends to the floating chat or the Chat page.
 */
const VoiceChatPage = () => {
  const voice = useVoiceSession();
  const systemName = useAppStore((s) => s.systemName) || brand.appName;
  const mode = useAppStore((s) => s.voiceActivationMode);
  const session = useVoiceSessionState(
    useShallow((s) => ({
      phase: s.phase,
      listenMode: s.listenMode,
      push: s.push,
      session: s.session,
      error: s.error,
      errorCode: s.errorCode,
      notice: s.notice,
    }))
  );
  const transcripts = useVoiceSessionState((s) => s.transcripts);
  const permission = useMicPermission();
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState(null);
  const [loading, setLoading] = useState(false);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    setStatusError(null);
    try {
      setStatus(await voiceService.getStatus());
    } catch (err) {
      setStatusError(err?.message || "Could not read the voice status");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const { title, hint } = describeVoicePhase(session, { systemName, mode });
  const piperVoices = status?.available_voices || [];
  const [permissionText, permissionTone] = PERMISSION_TEXT[permission] || PERMISSION_TEXT.unknown;

  return (
    <Paper sx={{ display: "flex", flexDirection: "column", height: "calc(100vh - 104px)", overflow: "hidden" }}>
      <Box sx={{ p: 3, borderBottom: 1, borderColor: "divider", display: "flex", alignItems: "center", gap: 2 }}>
        <MicNoneIcon sx={{ fontSize: 32, color: "primary.main" }} />
        <Box sx={{ flexGrow: 1 }}>
          <Typography variant="h4" component="h1" fontWeight="bold">
            Voice
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Talk with the mic in the top bar (or the sidebar). What you say goes to the floating chat, or to the
            Chat page when it is open, and replies are read aloud.
          </Typography>
        </Box>
        <GlobalMicButton variant="inline" label="Voice" showMenu={false} />
      </Box>

      <Box sx={{ flex: 1, overflow: "auto", p: 3, display: "grid", gap: 3, gridTemplateColumns: { xs: "1fr", lg: "1fr 1fr" }, alignContent: "start" }}>
        <Paper variant="outlined" sx={{ p: 2.5 }}>
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 1 }}>
            <Typography variant="h6" component="h2">
              This machine
            </Typography>
            <Button size="small" startIcon={loading ? <CircularProgress size={14} /> : <RefreshIcon />} onClick={loadStatus} disabled={loading}>
              Refresh
            </Button>
          </Box>
          {statusError && (
            <Alert severity="error" sx={{ mb: 1 }}>
              {statusError}
            </Alert>
          )}
          <StatusRow label="Microphone" value={permissionText} tone={permissionTone} />
          {status && (
            <>
              <StatusRow
                label="Speech recognition"
                value={status.speech_model_installed ? "Ready" : "Model not installed"}
                tone={status.speech_model_installed ? "success" : "warning"}
                detail={
                  status.speech_model_installed
                    ? `Whisper ${status.speech_model_id || ""} on this machine`
                    : "Install it in Settings → Voice"
                }
              />
              <StatusRow
                label="Spoken replies"
                value={piperVoices.length ? `${piperVoices.length} voice${piperVoices.length === 1 ? "" : "s"} installed` : "No voice installed"}
                tone={piperVoices.length ? "success" : "warning"}
                detail={
                  piperVoices.length
                    ? `${piperVoices.join(", ")}. Kokoro is used first while Audio Foundry runs.`
                    : "Install a voice in Settings → Voice, or start Audio Foundry."
                }
              />
              <StatusRow
                label="FFmpeg"
                value={status.ffmpeg_available ? "Installed" : "Missing"}
                tone={status.ffmpeg_available ? "success" : "warning"}
              />
            </>
          )}
          {status && (!status.speech_model_installed || !piperVoices.length) && (
            <Button component={RouterLink} to="/settings#settings-voice" size="small" sx={{ mt: 1 }}>
              Open voice settings
            </Button>
          )}

          <Divider sx={{ my: 2 }} />
          <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
            Right now: {title}
          </Typography>
          {hint && (
            <Typography variant="body2" color="text.secondary">
              {hint}
            </Typography>
          )}
          {session.error && (
            <Alert severity="warning" sx={{ mt: 1 }} onClose={() => voice.clearError()}>
              {session.error}
            </Alert>
          )}
          {session.notice && (
            <Typography variant="body2" color="warning.main" sx={{ mt: 1 }}>
              {session.notice.message}
            </Typography>
          )}

          <Divider sx={{ my: 2 }} />
          <Typography variant="h6" component="h2" sx={{ mb: 1 }}>
            Recently heard
          </Typography>
          {transcripts.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              Nothing yet. Click the mic and speak.
            </Typography>
          ) : (
            <List dense disablePadding>
              {transcripts.map((t, i) => (
                <ListItem key={`${t.at}-${i}`} disableGutters divider>
                  <ListItemText
                    primary={`“${t.text}”`}
                    secondary={`${new Date(t.at).toLocaleTimeString()} · ${OUTCOME_LABELS[t.outcome] || t.outcome}`}
                  />
                </ListItem>
              ))}
            </List>
          )}
        </Paper>

        <Paper variant="outlined" sx={{ p: 2.5 }}>
          <Typography variant="h6" component="h2" sx={{ mb: 1.5 }}>
            How the mic listens
          </Typography>
          <VoiceListeningSettings />
        </Paper>
      </Box>
    </Paper>
  );
};

export default VoiceChatPage;
