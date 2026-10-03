// The inbound guard in Settings → Agents: one switch for how it acts, and the
// list of changes it is holding. The guard itself lives in scripts/inbound_guard;
// this only shows its verdicts and records a person's answer to them.

import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { ActionButton, ChoiceChips, Cluster, Hint, Line, StatusPill } from "./ui";
import { inboundGuardService } from "../../api/inboundGuardService";
import { approveHeldChange, rejectHeldChange } from "../../api/heldChanges";

const MODES = [
  { value: "off", label: "Off", tooltip: "Nothing is read. Code lands exactly as before." },
  {
    value: "observe",
    label: "Observe",
    tooltip: "Every change the product writes into its own code is read and recorded; nothing is held.",
  },
  {
    value: "enforce",
    label: "Enforce",
    tooltip: "Risky changes wait here for your approval; clearly malicious ones are refused.",
  },
];

const SEVERITY_TONE = { critical: "error", high: "error", medium: "warning", low: "info", info: "default" };

export const FindingList = ({ findings, max = 50 }) => (
  <Stack spacing={0.75}>
    {(findings || []).slice(0, max).map((f, i) => (
      <Box key={`${f.rule}-${f.path}-${f.line}-${i}`}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Chip size="small" label={f.severity} color={SEVERITY_TONE[f.severity] || "default"} sx={{ height: 18 }} />
          <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
            {f.rule} · {f.line ? `${f.path}:${f.line}` : f.path}
          </Typography>
        </Stack>
        <Typography variant="body2" sx={{ ml: 0.5 }}>{f.why}</Typography>
        {f.excerpt && (
          <Typography
            variant="caption"
            component="pre"
            sx={{ ml: 0.5, m: 0, fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-all", color: "text.secondary" }}
          >
            {f.excerpt}
          </Typography>
        )}
      </Box>
    ))}
  </Stack>
);

function ReviewDialog({ open, onClose, onChanged }) {
  const [data, setData] = useState({ scans: [], git: [] });
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await inboundGuardService.listScans("open");
      setData(res?.data || { scans: [], git: [] });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      setSelected(null);
      setError(null);
      load();
    }
  }, [open, load]);

  const decide = async (decision, overrideBlock = false) => {
    setBusy(true);
    setError(null);
    try {
      if (decision === "approve") {
        await approveHeldChange(selected, { note, overrideBlock });
      } else {
        await rejectHeldChange(selected, { note });
      }
      setSelected(null);
      setNote("");
      await load();
      onChanged?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const items = [
    ...(data.scans || []).map((item) => ({ kind: "scan", key: `s${item.id}`, item })),
    ...(data.git || []).filter((g) => !g.approved).map((item) => ({ kind: "git", key: `g${item.digest}${item.ts}`, item })),
  ];
  const item = selected?.item;
  const blocked = item?.verdict === "block";

  return (
    <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>Held by the inbound guard</DialogTitle>
      <DialogContent dividers sx={{ display: "flex", gap: 2, minHeight: 360 }}>
        <Box sx={{ width: 320, flexShrink: 0, overflowY: "auto" }}>
          {loading && <CircularProgress size={18} />}
          {!loading && items.length === 0 && (
            <Typography variant="body2" color="text.secondary">Nothing is waiting.</Typography>
          )}
          {items.map((entry) => (
            <Box
              key={entry.key}
              onClick={() => setSelected(entry)}
              sx={{
                p: 1,
                mb: 0.5,
                borderRadius: 1,
                cursor: "pointer",
                bgcolor: selected?.key === entry.key ? "action.selected" : "transparent",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Typography variant="body2" sx={{ wordBreak: "break-all" }}>{entry.item.subject}</Typography>
              <Typography variant="caption" color="text.secondary">
                {entry.item.verdict} · {entry.item.source} · {(entry.item.findings || []).length} finding(s)
                {entry.kind === "git" ? " · git" : ""}
              </Typography>
            </Box>
          ))}
        </Box>
        <Divider orientation="vertical" flexItem />
        <Box sx={{ flex: 1, overflowY: "auto" }}>
          {!item ? (
            <Typography variant="body2" color="text.secondary">Pick a change to read what the guard found.</Typography>
          ) : (
            <Stack spacing={1.5}>
              <Typography variant="subtitle2" sx={{ wordBreak: "break-all" }}>{item.subject}</Typography>
              <Hint>
                {item.verdict === "block" ? "Blocked" : "Held"} · {item.source} · {item.files} file(s), +{item.added_lines} line(s) · digest {item.digest}
                {item.pending_fix_id ? ` · waiting as pending fix #${item.pending_fix_id} (Review fixes)` : ""}
                {selected.kind === "git" ? " · approving lets this exact change through the git hooks" : ""}
              </Hint>
              <FindingList findings={item.findings} />
              <TextField size="small" label="Note" value={note} onChange={(e) => setNote(e.target.value)} fullWidth />
              {error && <Alert severity="error">{error}</Alert>}
            </Stack>
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        {item && selected.kind === "scan" && (
          <ActionButton kind="destructive" onClick={() => decide("reject")} loading={busy}>Reject</ActionButton>
        )}
        {item && (
          <ActionButton
            kind="primary"
            onClick={() => decide("approve", blocked)}
            loading={busy}
            tooltip={blocked ? "This was blocked, not held: approve only after reading every finding." : ""}
          >
            {blocked ? "Approve despite the block" : item.landable || item.pending_fix_id ? "Approve and apply" : "Approve"}
          </ActionButton>
        )}
        <ActionButton onClick={onClose}>Close</ActionButton>
      </DialogActions>
    </Dialog>
  );
}

const describeSweep = (sweep) => {
  if (!sweep?.at) return "No sweep yet.";
  if (sweep.skipped) return `Sweep skipped ${new Date(sweep.at).toLocaleTimeString()}: ${sweep.skipped}.`;
  const parts = [`Last sweep ${new Date(sweep.at).toLocaleTimeString()}: ${sweep.files} files`];
  if (sweep.seeded) parts.push(`first read of the code (${sweep.audit_findings || 0} existing findings to read once)`);
  else parts.push(`${sweep.changed} changed`);
  if (sweep.held) parts.push(`${sweep.held} held`);
  return parts.join(", ") + ".";
};

export default function InboundGuardSection() {
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [sweeping, setSweeping] = useState(false);

  const sweepNow = async () => {
    setSweeping(true);
    try {
      await inboundGuardService.sweep();
    } catch (err) {
      setError(err.message);
    } finally {
      setSweeping(false);
      load();
    }
  };

  const load = useCallback(async () => {
    try {
      const res = await inboundGuardService.getState();
      setState(res?.data || null);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const changeMode = async (mode) => {
    const previous = state;
    setState((s) => ({ ...s, mode }));
    try {
      await inboundGuardService.setMode(mode);
      load();
    } catch (err) {
      setState(previous);
      setError(err.message);
    }
  };

  if (!state && !error) return <CircularProgress size={16} />;
  const open = state?.open || 0;
  const scanners = state?.providers?.scanners || [];

  return (
    <Cluster label="Inbound guard" note="reads code before it lands in this checkout">
      <Line>
        <ChoiceChips options={MODES} value={state?.mode || "off"} onChange={changeMode} ariaLabel="Inbound guard mode" />
        <StatusPill
          tone={open > 0 ? "warn" : "neutral"}
          label={open > 0 ? `${open} waiting` : "nothing waiting"}
        />
        <ActionButton kind="link" onClick={() => setReviewOpen(true)}>Review held changes</ActionButton>
        <ActionButton
          onClick={sweepNow}
          loading={sweeping}
          disabled={(state?.mode || "off") === "off"}
          tooltip="Read every watched file that changed since the last sweep: this checkout's code, ComfyUI custom nodes, extensions, the agent's notes."
        >
          Sweep now
        </ActionButton>
      </Line>
      <Hint>{describeSweep(state?.sweep)}</Hint>
      <Hint>
        Git hooks {state?.git?.installed ? `installed (${state.git.mode})` : "not installed — run scripts/install_hooks.sh"}
        {scanners.length ? ` · extra checks from ${scanners.join(", ")}` : ""}
      </Hint>
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
      <ReviewDialog open={reviewOpen} onClose={() => setReviewOpen(false)} onChanged={load} />
    </Cluster>
  );
}
