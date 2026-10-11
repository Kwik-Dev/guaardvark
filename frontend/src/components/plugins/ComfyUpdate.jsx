// frontend/src/components/plugins/ComfyUpdate.jsx
/**
 * The Update action on the ComfyUI plugin card: the installed version, an
 * Update (or Restore) button behind a confirmation, progress while the job
 * runs, and the result when it ends. Nothing runs until the person confirms.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Collapse, LinearProgress, Typography } from '@mui/material';
import { ActionButton, ConfirmActionDialog, Hint, Line, StatusPill } from '../settings/ui';
import {
  checkComfyUpdate,
  getComfyUpdateStatus,
  repairComfyUpdate,
  restoreComfyPrevious,
  startComfyUpdate,
} from '../../api/pluginsService';

const POLL_MS = 1000;

const RESULT = {
  succeeded: { tone: 'ok', label: 'Updated' },
  rolled_back: { tone: 'warn', label: 'Switched back' },
  failed: { tone: 'error', label: 'Failed' },
};

const versionArrow = (from, to) => `${from || '?'} → ${to || '?'}`;

const packageChange = ({ name, from, to }) => ({
  label: name,
  value: !from ? `new, ${to}` : !to ? `${from}, removed` : versionArrow(from, to),
});

const resultKey = (status) => `${status.state}:${status.finished_at || status.started_at || ''}`;

/**
 * State and actions for the ComfyUI update. Inert for any other plugin, so the
 * card can call it unconditionally.
 *
 * @param {object} plugin   the card's plugin (id, status)
 * @param {function} [showMessage]  snackbar (message, severity)
 */
export const useComfyUpdate = (plugin, showMessage) => {
  const active = plugin?.id === 'comfyui';
  const [check, setCheck] = useState(null);
  const [status, setStatus] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState(false);
  const [dismissed, setDismissed] = useState(null);
  const wasRunning = useRef(false);

  const refreshCheck = useCallback(async () => {
    if (!active) return;
    try {
      setCheck(await checkComfyUpdate());
    } catch {
      setCheck(null);
    }
  }, [active]);

  const refreshStatus = useCallback(async () => {
    if (!active) return;
    try {
      setStatus(await getComfyUpdateStatus());
    } catch {
      // Keep the last status; a transient error must not stop the progress view.
    }
  }, [active]);

  // The check depends on whether ComfyUI is running or busy, so it follows the card's status.
  useEffect(() => {
    refreshCheck();
  }, [refreshCheck, plugin?.status]);

  // A job started before this page was opened is picked up here.
  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  const running = status?.state === 'running';

  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(refreshStatus, POLL_MS);
    return () => clearInterval(timer);
  }, [running, refreshStatus]);

  useEffect(() => {
    if (running) {
      wasRunning.current = true;
      return;
    }
    if (wasRunning.current) {
      wasRunning.current = false;
      refreshCheck();
    }
  }, [running, refreshCheck]);

  const confirm = useCallback(async () => {
    const start = { update: startComfyUpdate, restore: restoreComfyPrevious, repair: repairComfyUpdate }[dialog];
    if (!start) return;
    setBusy(true);
    try {
      const started = await start();
      setStatus(started?.state ? started : { state: 'running', percent: 0, phase_label: 'Starting' });
      setDialog(null);
    } catch (err) {
      showMessage?.(err?.message || 'The update could not start.', 'error');
      setDialog(null);
      refreshCheck();
    } finally {
      setBusy(false);
    }
  }, [dialog, refreshCheck, showMessage]);

  const result = status && RESULT[status.state] && resultKey(status) !== dismissed ? status : null;

  // A crash mid-update left ComfyUI half switched; only Repair is offered until it is put back.
  const needsRepair = !running && (Boolean(check?.needs_repair) || status?.state === 'needs_repair');
  const repair = needsRepair
    ? {
        paths: check?.repair?.paths || [],
        message: check?.repair?.message || status?.message || 'The last update did not finish; ComfyUI needs to be put back.',
      }
    : null;

  return {
    active,
    check,
    status,
    running,
    result,
    dialog,
    busy,
    installed: check?.installed || null,
    available: Boolean(check?.available) && !running && !needsRepair,
    previous: (!running && !needsRepair && check?.previous) || null,
    blocked: check?.busy || null,
    repair,
    openUpdate: () => setDialog('update'),
    openRestore: () => setDialog('restore'),
    openRepair: () => setDialog('repair'),
    closeDialog: () => !busy && setDialog(null),
    confirm,
    dismissResult: () => status && setDismissed(resultKey(status)),
  };
};

const LogTail = ({ lines }) => {
  const [open, setOpen] = useState(false);
  if (!lines?.length) return null;
  return (
    <>
      <ActionButton kind="link" onClick={() => setOpen((v) => !v)} sx={{ alignSelf: 'flex-start' }}>
        {open ? 'Hide details' : 'Show details'}
      </ActionButton>
      <Collapse in={open}>
        <Box
          data-testid="comfyui-update-log"
          sx={{
            maxHeight: 160,
            overflow: 'auto',
            bgcolor: '#1e1e1e',
            color: '#d4d4d4',
            fontFamily: 'monospace',
            fontSize: '0.7rem',
            p: 1,
            borderRadius: 1,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          {lines.join('\n')}
        </Box>
      </Collapse>
    </>
  );
};

const Paths = ({ paths }) => (
  <Typography component="span" sx={{ fontFamily: 'monospace', fontSize: '0.7rem', wordBreak: 'break-all' }}>
    {paths.join(', ')}
  </Typography>
);

const RepairDialog = ({ comfy }) => {
  const { check, repair, dialog, busy, closeDialog, confirm } = comfy;
  const facts = [];
  if (check?.previous) facts.push({ label: 'Version', value: versionArrow(check.installed, check.previous) });
  if (repair?.paths?.length) facts.push({ label: 'Moves back', value: <Paths paths={repair.paths} /> });
  return (
    <ConfirmActionDialog
      open={dialog === 'repair'}
      title="Put ComfyUI back the way it was"
      description="Moves your models and settings back to the previous copy of ComfyUI and restarts that version. It takes a few minutes."
      facts={facts}
      keeps={check?.keeps?.length ? check.keeps.join(', ') : undefined}
      confirmLabel="Repair now"
      busy={busy}
      reversible
      onConfirm={confirm}
      onClose={closeDialog}
    />
  );
};

const UpdateDialog = ({ comfy }) => {
  const { check, dialog, busy, closeDialog, confirm } = comfy;
  if (dialog === 'repair') return <RepairDialog comfy={comfy} />;
  if (!check) return null;
  const restore = dialog === 'restore';
  const target = restore ? check.previous : check.target;
  const facts = [{ label: 'Version', value: versionArrow(check.installed, target) }];
  if (!restore) {
    facts.push(...(check.package_changes || []).map(packageChange));
    if (check.download) facts.push({ label: 'Download', value: check.download });
  }
  if (typeof check.disk_free_gb === 'number') {
    facts.push({ label: 'Free disk', value: `${check.disk_free_gb.toFixed(1)} GB` });
  }
  const description = restore
    ? `Puts the kept copy of ComfyUI ${target} back in place of ${check.installed}. ComfyUI stops for a few minutes while it switches over.`
    : `Downloads ComfyUI ${target} from GitHub and its Python packages from PyPI. ComfyUI stops for a few minutes while it switches over. If anything fails, it switches back to ${check.installed} by itself.`;
  return (
    <ConfirmActionDialog
      open={dialog !== null}
      title={restore ? `Restore ComfyUI ${target}` : `Update ComfyUI to ${target}`}
      description={description}
      facts={facts}
      keeps={check.keeps?.length ? check.keeps.join(', ') : undefined}
      confirmLabel={restore ? 'Restore now' : 'Update now'}
      busy={busy}
      reversible
      onConfirm={confirm}
      onClose={closeDialog}
    />
  );
};

/**
 * The card section below the version chips: buttons, progress or result.
 *
 * @param {ReturnType<typeof useComfyUpdate>} comfy
 */
export const ComfyUpdateSection = ({ comfy }) => {
  const { active, check, status, running, result, available, previous, blocked, repair } = comfy;
  if (!active || (!check && !running && !result && !repair)) return null;

  const percent = Math.max(0, Math.min(100, Number(status?.percent) || 0));

  return (
    <Box data-testid="comfyui-update" sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, mb: 1 }}>
      {repair && (
        <Box data-testid="comfyui-update-repair" sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
          <Hint sx={{ color: 'warning.main' }}>{repair.message}</Hint>
          {repair.paths.length > 0 && <Paths paths={repair.paths} />}
          <Line>
            <ActionButton
              onClick={comfy.openRepair}
              disabled={Boolean(blocked)}
              tooltip={blocked || 'Put the previous copy of ComfyUI back and restart it'}
            >
              Repair
            </ActionButton>
          </Line>
        </Box>
      )}

      {(available || previous) && (
        <Line>
          {available && (
            <ActionButton
              onClick={comfy.openUpdate}
              disabled={Boolean(blocked)}
              tooltip={blocked || `Download ComfyUI ${check.target} and switch to it`}
            >
              Update to {check.target}
            </ActionButton>
          )}
          {previous && (
            <ActionButton
              kind="link"
              onClick={comfy.openRestore}
              disabled={Boolean(blocked)}
              tooltip={blocked || `Put the kept copy of ComfyUI ${previous} back`}
            >
              Restore {previous}
            </ActionButton>
          )}
        </Line>
      )}

      {running && (
        <Box data-testid="comfyui-update-progress" sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
          <Typography variant="caption" color="text.secondary">
            {`ComfyUI ${versionArrow(status.from, status.to)}: ${status.phase_label || 'working'} (${percent}%)`}
          </Typography>
          <LinearProgress variant={percent > 0 ? 'determinate' : 'indeterminate'} value={percent} />
          <LogTail lines={status.log_tail} />
        </Box>
      )}

      {!running && result && (
        <Box data-testid="comfyui-update-result" sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
          <Line>
            <StatusPill label={RESULT[result.state].label} tone={RESULT[result.state].tone} />
            {result.message && <Hint>{result.message}</Hint>}
            <ActionButton kind="link" onClick={comfy.dismissResult}>
              Dismiss
            </ActionButton>
          </Line>
          <LogTail lines={result.log_tail} />
        </Box>
      )}

      <UpdateDialog comfy={comfy} />
    </Box>
  );
};

export default ComfyUpdateSection;
