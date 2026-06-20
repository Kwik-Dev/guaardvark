// frontend/src/pages/PluginsPage.jsx
/**
 * Plugin Management Page
 * Plugin cards with on/off toggles, VRAM budget bar, and per-plugin log viewer.
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Box,
  Typography,
  Paper,
  Grid,
  Card,
  CardContent,
  CardActions,
  Button,
  Chip,
  Switch,
  FormControlLabel,
  IconButton,
  Tooltip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  CircularProgress,
  Alert,
  Divider,
  Stack,
  Collapse,
} from '@mui/material';
import {
  PlayArrow as StartIcon,
  Stop as StopIcon,
  Refresh as RefreshIcon,
  Settings as SettingsIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  CheckCircle as HealthyIcon,
  Error as ErrorIcon,
  HelpOutline as UnknownIcon,
  Memory as GpuIcon,
  Extension as PluginIcon,
  Terminal as LogIcon,
  Videocam as CameraOnIcon,
  VideocamOff as CameraOffIcon,
} from '@mui/icons-material';
import { useSnackbar } from '../components/common/SnackbarProvider';
import PageLayout from '../components/layout/PageLayout';
import { ContextualLoader } from '../components/common/LoadingStates';
import { io } from 'socket.io-client';
import { SOCKET_URL } from '../api/apiClient';
import {
  listPlugins,
  startPlugin,
  stopPlugin,
  enablePlugin,
  disablePlugin,
  refreshPlugins,
  updatePluginConfig,
  getPluginLogs,
  startVisionCamera,
  stopVisionCamera,
  getVisionCameraStatus,
} from '../api/pluginsService';
import { getGpuStatus } from '../api/gpuService';

// ── Constants ──────────────────────────────────────────────────────────
const TOTAL_VRAM_MB = 16384; // 16GB
const STATUS_CONFIG = {
  running: { color: 'success', icon: HealthyIcon, label: 'Running' },
  stopped: { color: 'default', icon: StopIcon, label: 'Stopped' },
  starting: { color: 'warning', icon: CircularProgress, label: 'Starting' },
  stopping: { color: 'warning', icon: CircularProgress, label: 'Stopping' },
  error: { color: 'error', icon: ErrorIcon, label: 'Error' },
  disabled: { color: 'default', icon: UnknownIcon, label: 'Disabled' },
  unknown: { color: 'default', icon: UnknownIcon, label: 'Unknown' },
};

const PLUGIN_COLORS = {
  ollama: '#7c4dff',
  comfyui: '#00c853',
  gpu_embedding: '#ff6d00',
};

// A plugin is "GPU-heavy" — and thus mutually exclusive with other heavy
// plugins (only one heavy GPU service at a time on a 16 GB card) — when its
// manifest VRAM estimate crosses this line. Driven by vram_estimate_mb so new
// plugins are covered automatically: ollama (8 GB), comfyui (6 GB),
// audio_foundry (10 GB), lora_trainer (18 GB) qualify; light GPU users
// (vision ~2 GB, upscaling ~1.5 GB) and CPU-only plugins fall below it and
// never trigger the swap prompt. Groundwork for the future 3-mode "gear" selector.
const GPU_HEAVY_THRESHOLD_MB = 3000;
const isGpuHeavy = (plugin) => (plugin?.vram_estimate_mb || 0) >= GPU_HEAVY_THRESHOLD_MB;

// ── VRAM Budget Bar ────────────────────────────────────────────────────
const VramBudgetBar = ({ plugins, gpuVram }) => {
  const totalMb = gpuVram?.total_mb || TOTAL_VRAM_MB;
  const usedMb = gpuVram?.used_mb || 0;
  const freeMb = gpuVram?.free_mb ?? (totalMb - usedMb);
  const usedPct = (usedMb / totalMb) * 100;

  const activePlugins = plugins.filter(
    (p) => p.status === 'running' && p.vram_estimate_mb > 0
  );

  return (
    <Paper sx={{ p: 2, mb: 3 }}>
      {/* Header */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <GpuIcon fontSize="small" />
          <Typography variant="subtitle2">
            GPU VRAM {gpuVram?.gpu_name ? `— ${gpuVram.gpu_name}` : ''}
          </Typography>
        </Box>
        <Stack direction="row" spacing={2} alignItems="center">
          {gpuVram?.utilization_percent != null && (
            <Chip
              size="small"
              label={`${gpuVram.utilization_percent}% util`}
              color={gpuVram.utilization_percent > 80 ? 'warning' : 'default'}
              variant="outlined"
            />
          )}
          <Typography variant="body2" color="text.secondary">
            {(usedMb / 1024).toFixed(1)} / {(totalMb / 1024).toFixed(1)} GB
          </Typography>
        </Stack>
      </Box>

      {/* Live usage bar */}
      <Box
        sx={{
          height: 24,
          borderRadius: 1,
          bgcolor: 'action.hover',
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        <Box
          sx={{
            width: `${usedPct}%`,
            height: '100%',
            bgcolor: usedPct > 90 ? 'error.main' : usedPct > 70 ? 'warning.main' : 'primary.main',
            transition: 'width 0.5s ease',
          }}
        />
        {/* Estimated segments overlay */}
        <Box
          sx={{
            position: 'absolute',
            top: 0,
            left: 0,
            height: '100%',
            display: 'flex',
            pointerEvents: 'none',
          }}
        >
          {activePlugins.map((p) => {
            const pct = (p.vram_estimate_mb / totalMb) * 100;
            return (
              <Box
                key={p.id}
                sx={{
                  width: `${pct}%`,
                  height: '100%',
                  borderRight: '2px solid rgba(255,255,255,0.4)',
                }}
              />
            );
          })}
        </Box>
      </Box>

      {/* Legend + free VRAM */}
      <Stack direction="row" spacing={2} sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
          {(freeMb / 1024).toFixed(1)} GB free
        </Typography>
        {activePlugins.map((p) => (
          <Box key={p.id} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Box
              sx={{
                width: 10,
                height: 10,
                borderRadius: '50%',
                bgcolor: PLUGIN_COLORS[p.id] || '#90a4ae',
              }}
            />
            <Typography variant="caption" color="text.secondary">
              {p.name} (~{(p.vram_estimate_mb / 1024).toFixed(1)}GB est.)
            </Typography>
          </Box>
        ))}
      </Stack>

      {usedPct > 90 && (
        <Alert severity="warning" sx={{ mt: 1 }} variant="outlined">
          VRAM usage is near capacity. Stop unused services from this page to free memory.
        </Alert>
      )}
    </Paper>
  );
};

// ── Log Viewer ─────────────────────────────────────────────────────────
const LogViewer = ({ pluginId, open }) => {
  const [logs, setLogs] = useState('');
  const [loading, setLoading] = useState(false);
  const logRef = useRef(null);

  const fetchLogs = useCallback(async () => {
    if (!open) return;
    setLoading(true);
    try {
      const response = await getPluginLogs(pluginId, 200);
      if (response.success) {
        setLogs(response.data.logs || '(no logs)');
      }
    } catch {
      setLogs('(failed to fetch logs)');
    } finally {
      setLoading(false);
    }
  }, [pluginId, open]);

  useEffect(() => {
    fetchLogs();
    if (!open) return;
    const interval = setInterval(fetchLogs, 5000);
    return () => clearInterval(interval);
  }, [fetchLogs, open]);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  if (!open) return null;

  return (
    <Box sx={{ mt: 1 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 0.5 }}>
        <Typography variant="caption" color="text.secondary">
          Logs (last 200 lines)
        </Typography>
        <IconButton size="small" onClick={fetchLogs} disabled={loading}>
          <RefreshIcon fontSize="small" />
        </IconButton>
      </Box>
      <Box
        ref={logRef}
        sx={{
          maxHeight: 240,
          overflow: 'auto',
          bgcolor: '#1e1e1e',
          color: '#d4d4d4',
          fontFamily: 'monospace',
          fontSize: '0.75rem',
          p: 1.5,
          borderRadius: 1,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
        }}
      >
        {loading && !logs ? 'Loading...' : logs}
      </Box>
    </Box>
  );
};

// ── Plugin Card ────────────────────────────────────────────────────────
const PluginCard = ({ plugin, onAction, onConfigOpen, showMessage }) => {
  const [expanded, setExpanded] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [actionLoading, setActionLoading] = useState(null);

  // Camera state (vision_pipeline only)
  const [cameraActive, setCameraActive] = useState(false);
  const [cameraLoading, setCameraLoading] = useState(false);

  useEffect(() => {
    if (plugin.id !== 'vision_pipeline' || plugin.status !== 'running') {
      setCameraActive(false);
      return;
    }
    const fetchStatus = async () => {
      try {
        const resp = await getVisionCameraStatus();
        setCameraActive(resp?.active || false);
      } catch {
        setCameraActive(false);
      }
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, [plugin.id, plugin.status]);

  const handleCameraToggle = async () => {
    setCameraLoading(true);
    try {
      if (cameraActive) {
        await stopVisionCamera();
        setCameraActive(false);
        if (showMessage) showMessage('Camera stopped', 'info');
      } else {
        await startVisionCamera(0);
        setCameraActive(true);
        if (showMessage) showMessage('Camera started', 'success');
      }
    } catch (err) {
      if (showMessage) showMessage(err.message || 'Camera action failed', 'error');
    } finally {
      setCameraLoading(false);
    }
  };

  const statusConfig = STATUS_CONFIG[plugin.status] || STATUS_CONFIG.unknown;
  const StatusIcon = statusConfig.icon;
  const accentColor = PLUGIN_COLORS[plugin.id] || '#90a4ae';

  const handleAction = async (action) => {
    setActionLoading(action);
    try {
      await onAction(plugin.id, action);
    } finally {
      setActionLoading(null);
    }
  };

  const isLoading = actionLoading !== null;

  return (
    <Card
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        // Opacity reflects the actual running state, not the enabled config flag.
        // Stopped plugins look dim regardless of whether they're "enabled" — what
        // matters to the user is whether the plugin is actually doing anything.
        opacity: plugin.status === 'running' ? 1 : 0.6,
        borderLeft: `4px solid ${plugin.status === 'running' ? accentColor : 'transparent'}`,
      }}
    >
      <CardContent sx={{ flexGrow: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', mb: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <PluginIcon sx={{ color: accentColor }} />
            <Typography variant="h6" component="div">
              {plugin.name}
            </Typography>
          </Box>
          <Chip
            size="small"
            label={statusConfig.label}
            color={statusConfig.color}
            icon={
              plugin.status === 'starting' || plugin.status === 'stopping' ? (
                <CircularProgress size={12} color="inherit" />
              ) : (
                <StatusIcon fontSize="small" />
              )
            }
          />
        </Box>

        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {plugin.description || 'No description available.'}
        </Typography>

        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
          <Chip size="small" label={`v${plugin.version}`} variant="outlined" />
          <Chip size="small" label={plugin.type} variant="outlined" />
          {plugin.port && (
            <Chip size="small" label={`Port ${plugin.port}`} variant="outlined" />
          )}
          {plugin.vram_estimate_mb > 0 && (
            <Chip
              size="small"
              icon={<GpuIcon />}
              label={`~${(plugin.vram_estimate_mb / 1024).toFixed(1)} GB VRAM`}
              variant="outlined"
              color={plugin.status === 'running' ? 'primary' : 'default'}
            />
          )}
        </Stack>

        <Collapse in={expanded}>
          <Divider sx={{ my: 1.5 }} />
          <Typography variant="body2" color="text.secondary">
            Category: {plugin.category}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            ID: {plugin.id}
          </Typography>
          {plugin.plugin_dir && (
            <Typography variant="body2" color="text.secondary" sx={{ wordBreak: 'break-all' }}>
              Path: {plugin.plugin_dir}
            </Typography>
          )}
        </Collapse>

        <Collapse in={logsOpen}>
          <LogViewer pluginId={plugin.id} open={logsOpen} />
        </Collapse>
      </CardContent>

      <Divider />

      <CardActions sx={{ justifyContent: 'space-between', px: 2 }}>
        <Tooltip
          title={
            (plugin.cooldown_remaining || 0) > 0
              ? `Cooling down — wait ${Math.ceil(plugin.cooldown_remaining)}s before toggling again`
              : !plugin.enabled
                ? 'Currently disabled. Toggling on will both start it AND re-enable it across restarts.'
                : 'Preference saved across restarts'
          }
          arrow
        >
          <FormControlLabel
            control={
              <Switch
                checked={!!plugin.enabled}
                // The switch reflects the persistent user preference (enabled flag from
                // the user_enabled overlay in data/plugin_state.json). This ensures the
                // toggle updates visually when enable/disable succeeds (via socket
                // snapshot or optimistic update). Runtime 'status' (running/stopped/disabled)
                // drives the Chip and card opacity instead.
                onChange={() => handleAction(plugin.enabled ? 'disable' : 'start')}
                disabled={
                  isLoading
                  || plugin.status === 'starting'
                  || plugin.status === 'stopping'
                  || (plugin.cooldown_remaining || 0) > 0
                }
                size="small"
                color="success"
              />
            }
            label={
              (plugin.cooldown_remaining || 0) > 0
                ? `${plugin.enabled ? 'On' : 'Off'} (cooling ${Math.ceil(plugin.cooldown_remaining)}s)`
                : (plugin.enabled ? 'On' : 'Off')
            }
          />
        </Tooltip>

        <Box sx={{ display: 'flex', gap: 0.5 }}>

          {plugin.id === 'vision_pipeline' && plugin.status === 'running' && (
            <Tooltip title={cameraActive ? 'Stop Camera' : 'Start Camera'}>
              <IconButton
                size="small"
                color={cameraActive ? 'primary' : 'default'}
                onClick={handleCameraToggle}
                disabled={cameraLoading}
              >
                {cameraLoading ? (
                  <CircularProgress size={20} />
                ) : cameraActive ? (
                  <CameraOnIcon />
                ) : (
                  <CameraOffIcon />
                )}
              </IconButton>
            </Tooltip>
          )}

          <Tooltip title="Logs">
            <IconButton size="small" onClick={() => setLogsOpen(!logsOpen)}>
              <LogIcon fontSize="small" color={logsOpen ? 'primary' : 'action'} />
            </IconButton>
          </Tooltip>

          <Tooltip title="Settings">
            <IconButton size="small" onClick={() => onConfigOpen(plugin)} disabled={isLoading}>
              <SettingsIcon />
            </IconButton>
          </Tooltip>

          <Tooltip title={expanded ? 'Less' : 'More'}>
            <IconButton size="small" onClick={() => setExpanded(!expanded)}>
              {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            </IconButton>
          </Tooltip>
        </Box>
      </CardActions>
    </Card>
  );
};

// ── Config Dialog ──────────────────────────────────────────────────────
const ConfigDialog = ({ open, plugin, onClose, onSave }) => {
  const [config, setConfig] = useState({});
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (plugin?.config) setConfig({ ...plugin.config });
  }, [plugin]);

  const handleSave = async () => {
    setLoading(true);
    try {
      await onSave(plugin.id, config);
      onClose();
    } finally {
      setLoading(false);
    }
  };

  if (!plugin) return null;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Configure: {plugin.name}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {plugin.config?.service_url !== undefined && (
            <TextField
              label="Service URL"
              value={config.service_url || ''}
              onChange={(e) => setConfig({ ...config, service_url: e.target.value })}
              fullWidth
              size="small"
            />
          )}
          {plugin.config?.timeout !== undefined && (
            <TextField
              label="Timeout (seconds)"
              type="number"
              value={config.timeout || 30}
              onChange={(e) => setConfig({ ...config, timeout: parseInt(e.target.value) || 30 })}
              fullWidth
              size="small"
            />
          )}
          {plugin.config?.fallback_enabled !== undefined && (
            <FormControlLabel
              control={
                <Switch
                  checked={config.fallback_enabled ?? true}
                  onChange={(e) => setConfig({ ...config, fallback_enabled: e.target.checked })}
                />
              }
              label="Enable fallback to CPU"
            />
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>Cancel</Button>
        <Button onClick={handleSave} variant="contained" disabled={loading}>
          {loading ? <CircularProgress size={20} /> : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

// ── Restart Ollama Dialog ─────────────────────────────────────────────
const RestartOllamaDialog = ({ open, onClose, onConfirm, loading }) => {
  return (
    <Dialog open={open} onClose={loading ? undefined : onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Restart Ollama?</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary">
          ComfyUI has been stopped. Would you like to restart Ollama so that
          chat, RAG, and other AI features are available again?
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>No thanks</Button>
        <Button
          onClick={onConfirm}
          variant="contained"
          disabled={loading}
          startIcon={loading ? <CircularProgress size={16} /> : <StartIcon />}
        >
          {loading ? 'Starting...' : 'Start Ollama'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

// ── Page ───────────────────────────────────────────────────────────────
const applyPluginsPayload = (payload, setPlugins, setLoading, setError) => {
  const list = payload?.plugins;
  if (!Array.isArray(list)) return;
  setPlugins(list);
  setLoading(false);
  setError(null);
};

const PluginsPage = () => {
  const [plugins, setPlugins] = useState([]);
  const [gpuVram, setGpuVram] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [configPlugin, setConfigPlugin] = useState(null);
  const { showMessage } = useSnackbar();
  const socketRef = useRef(null);
  const cooldownTimerRef = useRef(null);

  // Restart Ollama dialog state (shown after stopping ComfyUI)
  const [showRestartOllama, setShowRestartOllama] = useState(false);
  const [restartOllamaLoading, setRestartOllamaLoading] = useState(false);

  const fetchPlugins = useCallback(async () => {
    try {
      setError(null);
      const response = await listPlugins();
      if (response.success) {
        const list = response.data.plugins || [];
        setPlugins(list);
        // Return the fresh list so callers awaiting a refresh can read it
        // without racing the React state update (which is async and would
        // hand them the pre-refresh array via the closure).
        return list;
      } else {
        setError(response.message || 'Failed to load plugins');
      }
    } catch (err) {
      setError(err.message || 'Failed to load plugins');
    } finally {
      setLoading(false);
    }
    return null;
  }, []);

  // One-shot HTTP load + Socket.IO pushes (no polling — keeps logs quiet).
  useEffect(() => {
    fetchPlugins();

    const loadGpuOnce = async () => {
      try {
        const data = await getGpuStatus();
        if (data?.vram) setGpuVram(data.vram);
      } catch { /* gpu card optional */ }
    };
    loadGpuOnce();

    const socket = io(SOCKET_URL, {
      reconnection: true,
      reconnectionAttempts: 5,
      transports: ['websocket', 'polling'],
    });
    socketRef.current = socket;

    socket.on('connect', () => {
      socket.emit('subscribe_plugins');
      socket.emit('subscribe_gpu');
    });

    socket.on('plugins:status', (payload) => {
      applyPluginsPayload(payload, setPlugins, setLoading, setError);
    });

    socket.on('gpu:status', (payload) => {
      if (payload?.vram) setGpuVram(payload.vram);
    });

    return () => {
      socket.disconnect();
      socketRef.current = null;
    };
  }, [fetchPlugins]);

  // Local 1s countdown for gate cooldowns (no HTTP re-fetch).
  useEffect(() => {
    const hasCooldown = plugins.some((p) => (p.cooldown_remaining || 0) > 0);
    if (!hasCooldown) {
      if (cooldownTimerRef.current) {
        clearInterval(cooldownTimerRef.current);
        cooldownTimerRef.current = null;
      }
      return undefined;
    }
    if (cooldownTimerRef.current) return undefined;

    cooldownTimerRef.current = setInterval(() => {
      setPlugins((prev) => {
        const next = prev.map((p) => ({
          ...p,
          cooldown_remaining: Math.max(0, (p.cooldown_remaining || 0) - 1),
        }));
        if (!next.some((p) => (p.cooldown_remaining || 0) > 0) && cooldownTimerRef.current) {
          clearInterval(cooldownTimerRef.current);
          cooldownTimerRef.current = null;
        }
        return next;
      });
    }, 1000);

    return () => {
      if (cooldownTimerRef.current) {
        clearInterval(cooldownTimerRef.current);
        cooldownTimerRef.current = null;
      }
    };
  }, [plugins]);

  // Find a running GPU-heavy plugin that contends for VRAM with the one being
  // started. At most one heavy plugin runs at a time, so the first match is the
  // one to swap out.
  const findGpuConflict = useCallback((pluginId) => {
    const requested = plugins.find((p) => p.id === pluginId);
    if (!isGpuHeavy(requested)) return null;
    return plugins.find(
      (p) => p.id !== pluginId && isGpuHeavy(p) && p.status === 'running'
    );
  }, [plugins]);

  const handlePluginAction = async (pluginId, action) => {
    try {
      let response;
      switch (action) {
        case 'start': {
          // GPU-heavy plugins contend for VRAM (only one really fits on a 16 GB
          // card). Rather than block with a swap modal, give the operator the
          // benefit of the doubt: start what they asked for and drop a temporary
          // heads-up naming the other GPU plugin so they can toggle it off
          // themselves if VRAM runs short.
          const conflict = findGpuConflict(pluginId);
          if (conflict) {
            const requestedName = plugins.find((p) => p.id === pluginId)?.name || pluginId;
            showMessage(
              `${conflict.name} is also using the GPU — toggle it off if ${requestedName} runs low on VRAM.`,
              'info'
            );
          }
          // Auto-enable if disabled so start (which rejects disabled plugins)
          // doesn't 400. This is the step the old swap handler skipped.
          if (!plugins.find((p) => p.id === pluginId)?.enabled) {
            await enablePlugin(pluginId);
          }
          response = await startPlugin(pluginId);
          break;
        }
        case 'stop': response = await stopPlugin(pluginId); break;
        case 'enable': response = await enablePlugin(pluginId); break;
        case 'disable': response = await disablePlugin(pluginId); break;
        default: throw new Error(`Unknown action: ${action}`);
      }
      if (response.success) {
        // Backend returned 200. Two sub-cases:
        //  1. Operation succeeded → data.success === true
        //  2. Operation was rate-limited by the traffic light → data.gated === true
        const data = response.data || {};
        if (data.gated) {
          // Friendly cooldown notice, not an error
          const cd = data.cooldown_remaining ? Math.ceil(data.cooldown_remaining) : null;
          const suffix = cd ? ` (wait ${cd}s)` : '';
          showMessage(`${data.error || 'Cooling down'}${suffix}`, 'warning');
          if (cd) {
            setPlugins((prev) => prev.map((p) => (
              p.id === pluginId ? { ...p, cooldown_remaining: cd } : p
            )));
          }
          return;
        }
        showMessage(response.message || `Plugin ${action} successful`, 'success');

        // Optimistic update: flip the enabled preference and status immediately so
        // the toggle switch in the UI reflects the change without waiting for the
        // 'plugins:status' socket round-trip. The authoritative snapshot from the
        // backend (via socket or later fetch) will reconcile any details (e.g. real
        // status after start completes, cooldowns, etc.).
        if (action === 'enable' || action === 'disable') {
          const newEnabled = action === 'enable';
          setPlugins((prev) => prev.map((p) =>
            p.id === pluginId
              ? { ...p, enabled: newEnabled, status: newEnabled ? 'stopped' : 'disabled', running: false }
              : p
          ));
        } else if (action === 'start') {
          setPlugins((prev) => prev.map((p) =>
            p.id === pluginId ? { ...p, status: 'starting', running: false } : p
          ));
        } else if (action === 'stop') {
          setPlugins((prev) => prev.map((p) =>
            p.id === pluginId ? { ...p, status: 'stopping', running: false } : p
          ));
        }

        // After stopping ComfyUI, offer to restart Ollama once socket/HTTP state settles.
        if (action === 'stop' && pluginId === 'comfyui') {
          setTimeout(async () => {
            const list = (await fetchPlugins()) || plugins;
            const ollamaPlugin = list.find((p) => p.id === 'ollama');
            if (ollamaPlugin?.enabled && ollamaPlugin.status !== 'running') {
              setShowRestartOllama(true);
            }
          }, 400);
        }
      } else {
        showMessage(response.message || `Failed to ${action} plugin`, 'error');
      }
    } catch (err) {
      showMessage(err.message || `Failed to ${action} plugin`, 'error');
    }
  };

  // Handle Restart Ollama confirmation
  const handleRestartOllama = async () => {
    setRestartOllamaLoading(true);
    try {
      // Make sure Ollama is enabled first
      const ollamaPlugin = plugins.find((p) => p.id === 'ollama');
      if (ollamaPlugin && !ollamaPlugin.enabled) {
        await enablePlugin('ollama');
      }
      const response = await startPlugin('ollama');
      if (response.success) {
        showMessage('Ollama started', 'success');
      } else {
        showMessage(response.message || 'Failed to start Ollama', 'error');
      }
    } catch (err) {
      showMessage(err.message || 'Failed to start Ollama', 'error');
    } finally {
      setRestartOllamaLoading(false);
      setShowRestartOllama(false);
      fetchPlugins();
    }
  };

  const handleRefresh = async () => {
    try {
      const response = await refreshPlugins();
      if (response.success) {
        showMessage(`Found ${response.data.count} plugins`, 'success');
        fetchPlugins();
      } else {
        showMessage('Failed to refresh plugins', 'error');
      }
    } catch (err) {
      showMessage(err.message || 'Failed to refresh plugins', 'error');
    }
  };

  const handleConfigSave = async (pluginId, config) => {
    try {
      const response = await updatePluginConfig(pluginId, config);
      if (response.success) {
        showMessage('Configuration saved', 'success');
        fetchPlugins();
      } else {
        showMessage('Failed to save configuration', 'error');
      }
    } catch (err) {
      showMessage(err.message || 'Failed to save configuration', 'error');
    }
  };

  return (
    <PageLayout
      title="Plugins"
      variant="standard"
      actions={
        <Button
          variant="outlined"
          startIcon={<RefreshIcon />}
          onClick={handleRefresh}
          disabled={loading}
        >
          Refresh
        </Button>
      }
    >
      <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
        Manage GPU services and optional features. Toggle plugins on/off to control VRAM usage.
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 3 }}>{error}</Alert>
      )}

      {/* VRAM Budget Bar */}
      {plugins.length > 0 && <VramBudgetBar plugins={plugins} gpuVram={gpuVram} />}

      {loading && plugins.length === 0 ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
          <ContextualLoader loading message="Loading plugins..." showProgress={false} inline />
        </Box>
      ) : plugins.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <PluginIcon sx={{ fontSize: 64, color: 'text.secondary', mb: 2 }} />
          <Typography variant="h6" gutterBottom>No Plugins Found</Typography>
          <Typography variant="body2" color="text.secondary">
            Add plugins to the /plugins/ directory and click Refresh.
          </Typography>
        </Paper>
      ) : (
        <Grid container spacing={3}>
          {plugins.map((plugin) => (
            <Grid item xs={12} sm={6} md={4} key={plugin.id}>
              <PluginCard
                plugin={plugin}
                onAction={handlePluginAction}
                onConfigOpen={setConfigPlugin}
                showMessage={showMessage}
              />
            </Grid>
          ))}
        </Grid>
      )}

      <ConfigDialog
        open={configPlugin !== null}
        plugin={configPlugin}
        onClose={() => setConfigPlugin(null)}
        onSave={handleConfigSave}
      />

      {/* Restart Ollama Dialog (after ComfyUI stopped) */}
      <RestartOllamaDialog
        open={showRestartOllama}
        onClose={() => setShowRestartOllama(false)}
        onConfirm={handleRestartOllama}
        loading={restartOllamaLoading}
      />
    </PageLayout>
  );
};

export default PluginsPage;
