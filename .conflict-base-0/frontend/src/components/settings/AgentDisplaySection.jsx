// frontend/src/components/settings/AgentDisplaySection.jsx
// Detector + installer for the Agent Vision Control virtual display stack
// (Xvfb, x11vnc, openbox, tint2, xdotool, scrot, browser, python mss).
//
// Mirrors VoiceSettingsContent's Whisper installer — same alert + button shape.

import React, { useEffect, useState, useCallback } from 'react';
import { Box, Button, CircularProgress, Typography, Tooltip } from '@mui/material';
import MuiAlert from '@mui/material/Alert';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import RefreshIcon from '@mui/icons-material/Refresh';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import StopIcon from '@mui/icons-material/Stop';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';

import {
  getDisplayStatus,
  installDisplay,
  startDisplay,
  stopDisplay,
} from '../../api/agentDisplayService';

// Friendly labels — keep them tight so the row renders cleanly.
const COMPONENT_LABELS = {
  Xvfb: 'Xvfb (virtual X server)',
  x11vnc: 'x11vnc (VNC bridge)',
  openbox: 'Openbox (window manager)',
  tint2: 'Tint2 (taskbar)',
  xdotool: 'xdotool (input synthesis)',
  scrot: 'scrot (screen capture fallback)',
  browser: 'Browser (Firefox / Chromium)',
  mss: 'mss (Python screen capture)',
  start_script: 'start_agent_display.sh',
  display_running: 'Display :99 is live',
};

const COMPONENT_ORDER = [
  'Xvfb', 'x11vnc', 'openbox', 'tint2', 'xdotool', 'scrot',
  'browser', 'mss', 'start_script', 'display_running',
];

const StatusRow = ({ label, ok, version, hint }) => (
  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.5 }}>
    {ok ? (
      <CheckCircleOutlineIcon fontSize="small" sx={{ color: 'success.main' }} />
    ) : (
      <ErrorOutlineIcon fontSize="small" sx={{ color: 'warning.main' }} />
    )}
    <Typography variant="body2" sx={{ flex: 1 }}>{label}</Typography>
    {version && (
      <Tooltip title={version}>
        <Typography variant="caption" color="text.secondary" sx={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {version}
        </Typography>
      </Tooltip>
    )}
    {!ok && hint && (
      <Typography variant="caption" color="warning.main">{hint}</Typography>
    )}
  </Box>
);

const AgentDisplaySection = ({ showMessage }) => {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [installing, setInstalling] = useState(false);
  // controlAction: null | "start" | "stop" | "restart" — drives the spinner
  // on whichever button the user clicked.
  const [controlAction, setControlAction] = useState(null);
  const [error, setError] = useState(null);
  // Set when the host needs a human to run apt itself (sudo wants a password).
  const [manualInstall, setManualInstall] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getDisplayStatus();
      setStatus(data);
      // Drop any stale manual-install prompt once apt is satisfied, so Recheck
      // after running the command by hand clears the banner.
      if (!(data.missing_apt_packages || []).length) {
        setManualInstall(null);
      }
    } catch (e) {
      setError(e.message || 'Failed to probe agent display');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onInstall = async () => {
    setInstalling(true);
    setManualInstall(null);
    // pkexec raises the password dialog on the desktop, which can land behind the
    // browser window — say so, or it just looks like the button hung.
    showMessage?.(
      status?.install_method === 'pkexec'
        ? 'Approve the password prompt on your desktop to continue installing.'
        : 'Installing agent display dependencies… apt-get may take a minute.',
      'info',
    );
    try {
      const result = await installDisplay();
      if (result.success) {
        if (result.already_installed) {
          showMessage?.('Agent Display dependencies already installed.', 'info');
        } else {
          showMessage?.('Agent Display dependencies installed.', 'success');
        }
      } else {
        showMessage?.(`Install failed: ${result.error}`, 'error');
      }
    } catch (e) {
      // handleResponse throws on non-2xx but preserves the JSON body on error.data.
      // A sudo-password host is the normal case, not a server fault: keep the exact
      // command on screen instead of burying it in a toast that disappears.
      const data = e.data || {};
      if (data.needs_manual_install && data.manual_command) {
        setManualInstall({ command: data.manual_command, reason: data.error || e.message });
      } else {
        showMessage?.(`Install failed: ${e.message}`, 'error');
      }
    } finally {
      setInstalling(false);
      refresh();
    }
  };

  const onCopyCommand = async (command) => {
    try {
      await navigator.clipboard.writeText(command);
      showMessage?.('Command copied to clipboard.', 'success');
    } catch (e) {
      // Clipboard needs a secure context; over plain http on a LAN IP it is absent.
      showMessage?.('Could not copy — select the command and copy it manually.', 'warning');
    }
  };

  const onStart = async () => {
    setControlAction('start');
    showMessage?.('Starting agent display…', 'info');
    try {
      const result = await startDisplay();
      if (result.success) {
        showMessage?.('Agent display is up on :99.', 'success');
      } else {
        showMessage?.(`Start failed: ${result.error}`, 'error');
      }
    } catch (e) {
      showMessage?.(`Start failed: ${e.message}`, 'error');
    } finally {
      setControlAction(null);
      refresh();
    }
  };

  const onStop = async () => {
    setControlAction('stop');
    showMessage?.('Stopping agent display…', 'info');
    try {
      const result = await stopDisplay({ force: true });
      if (result.success) {
        showMessage?.('Agent display stopped.', 'success');
      } else {
        showMessage?.(`Stop failed: ${result.error}`, 'error');
      }
    } catch (e) {
      showMessage?.(`Stop failed: ${e.message}`, 'error');
    } finally {
      setControlAction(null);
      refresh();
    }
  };

  const onRestart = async () => {
    setControlAction('restart');
    showMessage?.('Restarting agent display…', 'info');
    try {
      await stopDisplay({ force: true }).catch(() => {}); // tolerate stop failures (might already be down)
      const startResult = await startDisplay();
      if (startResult.success) {
        showMessage?.('Agent display restarted.', 'success');
      } else {
        showMessage?.(`Restart failed: ${startResult.error}`, 'error');
      }
    } catch (e) {
      showMessage?.(`Restart failed: ${e.message}`, 'error');
    } finally {
      setControlAction(null);
      refresh();
    }
  };

  if (loading && !status) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
        <CircularProgress size={20} />
      </Box>
    );
  }

  if (error) {
    return (
      <MuiAlert severity="error" sx={{ mb: 1 }}>
        {error}
      </MuiAlert>
    );
  }

  if (!status) return null;

  const components = status.components || {};
  const missingApt = status.missing_apt_packages || [];
  const missingPip = status.missing_pip_packages || [];
  const needsInstall = missingApt.length > 0 || missingPip.length > 0;
  // The backend reports this false when apt packages are needed but sudo would
  // prompt for a password — which a web service can never answer.
  const canAutoInstall = status.can_auto_install !== false;
  // Prefer the command from a failed attempt; otherwise the one the probe supplied.
  const pendingManual = manualInstall
    || (needsInstall && !canAutoInstall && status.manual_command
      ? {
        command: status.manual_command,
        reason: 'Installing system packages requires sudo, and this machine asks for a '
          + 'password. Guaardvark runs as a web service with no terminal, so it cannot '
          + 'answer that prompt. Run this in a terminal, then click Recheck.',
      }
      : null);

  return (
    <Box>
      {pendingManual ? (
        <MuiAlert severity="warning" sx={{ mb: 1.5 }}>
          <Typography variant="body2" sx={{ mb: 1 }}>{pendingManual.reason}</Typography>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 1,
              p: 1,
              borderRadius: 1,
              bgcolor: 'action.hover',
              fontFamily: 'monospace',
              fontSize: '0.8rem',
              overflowX: 'auto',
            }}
          >
            <Box component="code" sx={{ flex: 1, whiteSpace: 'pre' }}>
              {pendingManual.command}
            </Box>
            <Tooltip title="Copy command">
              <Button
                size="small"
                color="inherit"
                startIcon={<ContentCopyIcon fontSize="small" />}
                onClick={() => onCopyCommand(pendingManual.command)}
                sx={{ flexShrink: 0 }}
              >
                Copy
              </Button>
            </Tooltip>
          </Box>
          {missingPip.length > 0 && (
            <Typography variant="caption" sx={{ display: 'block', mt: 1 }}>
              The Python package{missingPip.length > 1 ? 's' : ''} ({missingPip.join(', ')})
              {' '}will install automatically — no sudo needed for that part.
            </Typography>
          )}
        </MuiAlert>
      ) : needsInstall ? (
        <MuiAlert
          severity="warning"
          sx={{ mb: 1.5 }}
          action={
            <Button
              color="inherit"
              size="small"
              startIcon={installing ? <CircularProgress size={16} color="inherit" /> : <FileDownloadIcon />}
              onClick={onInstall}
              disabled={installing}
            >
              {installing ? 'Installing…' : 'Install Missing'}
            </Button>
          }
        >
          Agent Display is missing components: {[...missingApt, ...missingPip].join(', ')}.
          {status.install_method === 'pkexec'
            ? ' Click to install — your desktop will ask for your password.'
            : ' Click to install via apt-get + pip.'}
        </MuiAlert>
      ) : status.display_running ? (
        <MuiAlert
          severity="success"
          sx={{ mb: 1.5 }}
          action={
            <Box sx={{ display: 'flex', gap: 0.5 }}>
              <Button
                color="inherit"
                size="small"
                startIcon={controlAction === 'restart' ? <CircularProgress size={16} color="inherit" /> : <RestartAltIcon />}
                onClick={onRestart}
                disabled={controlAction !== null}
              >
                {controlAction === 'restart' ? 'Restarting…' : 'Restart'}
              </Button>
              <Button
                color="inherit"
                size="small"
                startIcon={controlAction === 'stop' ? <CircularProgress size={16} color="inherit" /> : <StopIcon />}
                onClick={onStop}
                disabled={controlAction !== null}
              >
                {controlAction === 'stop' ? 'Stopping…' : 'Stop'}
              </Button>
            </Box>
          }
        >
          Agent Display is fully installed and running on :99.
        </MuiAlert>
      ) : (
        <MuiAlert
          severity="info"
          sx={{ mb: 1.5 }}
          action={
            <Button
              color="inherit"
              size="small"
              startIcon={controlAction === 'start' ? <CircularProgress size={16} color="inherit" /> : <PlayArrowIcon />}
              onClick={onStart}
              disabled={controlAction !== null}
            >
              {controlAction === 'start' ? 'Starting…' : 'Start Display'}
            </Button>
          }
        >
          All dependencies installed. Display :99 is not running yet.
        </MuiAlert>
      )}

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
        {COMPONENT_ORDER.map((key) => {
          const comp = components[key];
          if (!comp) return null;
          const label = COMPONENT_LABELS[key] || key;
          return (
            <StatusRow
              key={key}
              label={label}
              ok={!!comp.installed}
              version={comp.version}
              hint={!comp.installed && comp.apt_package ? `apt: ${comp.apt_package}` :
                    !comp.installed && comp.pip_package ? `pip: ${comp.pip_package}` : null}
            />
          );
        })}
      </Box>

      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 1 }}>
        <Button
          size="small"
          variant="text"
          startIcon={<RefreshIcon fontSize="small" />}
          onClick={refresh}
          disabled={loading || installing}
        >
          Recheck
        </Button>
      </Box>
    </Box>
  );
};

export default AgentDisplaySection;
