// frontend/src/components/settings/AgentDisplaySection.jsx
// Detector + installer for the Agent Vision Control virtual display stack
// (Xvfb, x11vnc, openbox, tint2, xdotool, scrot, browser, python mss).
//
// Mirrors VoiceSettingsContent's Whisper installer — same alert + button shape.

import React, { useEffect, useState, useCallback } from 'react';
import { Box, Button, CircularProgress, Collapse, Typography, Tooltip } from '@mui/material';
import MuiAlert from '@mui/material/Alert';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import RefreshIcon from '@mui/icons-material/Refresh';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import StopIcon from '@mui/icons-material/Stop';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';

import { ActionButton, Hint, Line, StatusPill } from './ui';
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
  const [showComponents, setShowComponents] = useState(false);

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
      // A stop the backend refuses (409: an agent task or training holds the
      // display) ends the restart here. Any other stop failure means it was
      // already down, so the start still runs.
      try {
        await stopDisplay({ force: true });
      } catch (stopErr) {
        if (stopErr?.status === 409) {
          showMessage?.(`Restart refused: ${stopErr.message}`, 'warning');
          return;
        }
      }
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
    return <CircularProgress size={16} />;
  }

  if (error) {
    return (
      <MuiAlert severity="error" sx={{ py: 0.25 }}>
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
  const isRunning = !!status.display_running;
  const pill = needsInstall
    ? { tone: 'warn', label: `Display :99 · ${[...missingApt, ...missingPip].length} missing` }
    : isRunning
      ? { tone: 'ok', label: 'Display :99 live' }
      : { tone: 'neutral', label: 'Display :99 not running' };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Line>
        <StatusPill tone={pill.tone} label={pill.label} />
        {needsInstall && !pendingManual && (
          <ActionButton
            startIcon={<FileDownloadIcon />}
            onClick={onInstall}
            loading={installing}
            tooltip={status.install_method === 'pkexec'
              ? 'Installs via apt-get and pip; your desktop will ask for your password.'
              : 'Installs via apt-get and pip.'}
          >
            Install missing
          </ActionButton>
        )}
        {!needsInstall && isRunning && (
          <>
            <ActionButton
              startIcon={<RestartAltIcon />}
              onClick={onRestart}
              loading={controlAction === 'restart'}
              disabled={controlAction !== null}
            >
              Restart
            </ActionButton>
            <ActionButton
              startIcon={<StopIcon />}
              onClick={onStop}
              loading={controlAction === 'stop'}
              disabled={controlAction !== null}
              tooltip="Stops Xvfb, x11vnc and the window manager. Refused while an agent task or training is using the display."
            >
              Stop
            </ActionButton>
          </>
        )}
        {!needsInstall && !isRunning && (
          <ActionButton
            startIcon={<PlayArrowIcon />}
            onClick={onStart}
            loading={controlAction === 'start'}
            disabled={controlAction !== null}
            tooltip="Starts Xvfb, x11vnc, openbox and tint2 on :99. Can take up to a minute."
          >
            Start display
          </ActionButton>
        )}
        <ActionButton kind="link" startIcon={<RefreshIcon />} onClick={refresh} disabled={loading || installing}>
          Recheck
        </ActionButton>
        <ActionButton kind="link" onClick={() => setShowComponents((v) => !v)}>
          {showComponents ? 'Hide components' : 'Components'}
        </ActionButton>
      </Line>

      {needsInstall && !pendingManual && (
        <Hint>Missing: {[...missingApt, ...missingPip].join(', ')}.</Hint>
      )}

      {pendingManual && (
        <MuiAlert severity="warning" sx={{ py: 0.5 }}>
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
      )}

      <Collapse in={showComponents} unmountOnExit>
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
      </Collapse>
    </Box>
  );
};

export default AgentDisplaySection;
