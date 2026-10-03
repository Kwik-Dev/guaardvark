// Code the inbound guard holds for a person: edits waiting as pending fixes,
// file operations, and changes the source watch found on disk. Approving lands
// the change first and records the approval after, so an item that can no
// longer land as it was held stays here with the reason.
import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  List,
  ListItemButton,
  ListItemText,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { Cancel as CancelIcon, CheckCircle as CheckCircleIcon } from "@mui/icons-material";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { a11yDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { inboundGuardService } from "../../api/inboundGuardService";
import { approveHeldChange, isActionable, rejectHeldChange } from "../../api/heldChanges";
import { FindingList } from "../settings/InboundGuardSection";
import { ConfirmActionDialog } from "../settings/ui";

const VERDICT_COLOR = { block: "error", hold: "warning" };

const KIND_LABEL = (scan) => {
  if (scan.pending_fix_id) return "edit";
  if (scan.landable) return "file";
  if (scan.source === "watch") return "on disk";
  return scan.source;
};

// What approving does depends on what was kept when the change was stopped. An
// edit refused outright (a block) keeps no text to apply; approving it records
// the digest, so the same change passes when it is made again.
const APPROVAL_EFFECT = (scan) => {
  if (!scan) return "apply";
  if (scan.source === "watch") return "accept";
  if (scan.pending_fix_id || scan.landable) return "apply";
  return "allow-again";
};

const APPROVED_MESSAGE = {
  apply: "Approved; the change landed",
  accept: "Accepted as it is on disk",
  "allow-again": "Approved; nothing was applied. The same change goes through if it is made again",
};

const formatWhen = (iso) => {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleDateString();
};

const HeldChangesPanel = ({ held, loading, onChanged, showMessage }) => {
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [confirming, setConfirming] = useState(false);

  const items = (held || []).filter(isActionable);

  const loadDetail = useCallback(async (id) => {
    setDetailLoading(true);
    setError(null);
    try {
      const res = await inboundGuardService.getScan(id);
      setDetail(res?.data || null);
    } catch (err) {
      setError(err.message);
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedId != null) loadDetail(selectedId);
    else setDetail(null);
  }, [selectedId, loadDetail]);

  useEffect(() => {
    if (selectedId != null && !items.some((s) => s.id === selectedId)) setSelectedId(null);
  }, [items, selectedId]);

  const act = async (decision) => {
    const entry = { kind: "scan", item: detail };
    setBusy(true);
    setError(null);
    try {
      if (decision === "approve") {
        await approveHeldChange(entry, { note, overrideBlock: detail.verdict === "block" });
        showMessage?.(APPROVED_MESSAGE[APPROVAL_EFFECT(detail)]);
      } else {
        await rejectHeldChange(entry, { note });
        showMessage?.("Rejected");
      }
      setNote("");
      setSelectedId(null);
      await onChanged?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  const renderList = () => {
    if (loading) {
      return (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress />
        </Box>
      );
    }
    if (!items.length) {
      return (
        <Alert severity="info" sx={{ m: 2 }}>
          No code changes are waiting. The first sweep's report and git records are under Settings → Agents →
          Inbound guard.
        </Alert>
      );
    }
    return (
      <List dense disablePadding sx={{ maxHeight: 520, overflowY: "auto" }}>
        {items.map((scan) => (
          <ListItemButton key={scan.id} selected={selectedId === scan.id} onClick={() => setSelectedId(scan.id)}>
            <ListItemText
              primary={
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="body2" noWrap sx={{ fontWeight: 500, minWidth: 0 }}>
                    {scan.subject}
                  </Typography>
                </Stack>
              }
              secondary={
                <Stack direction="row" spacing={1} alignItems="center" component="span">
                  <Chip
                    label={scan.verdict}
                    size="small"
                    color={VERDICT_COLOR[scan.verdict] || "default"}
                    sx={{ height: 18 }}
                  />
                  <Typography variant="caption" color="text.secondary" component="span">
                    {KIND_LABEL(scan)} · {scan.findings?.length || 0} finding(s) · {formatWhen(scan.created_at)}
                  </Typography>
                </Stack>
              }
              secondaryTypographyProps={{ component: "div" }}
            />
          </ListItemButton>
        ))}
      </List>
    );
  };

  const renderDetail = () => {
    if (selectedId == null) {
      return (
        <Typography variant="body2" color="text.secondary" sx={{ p: 3 }}>
          Select a change to read what the guard found.
        </Typography>
      );
    }
    if (detailLoading || !detail) {
      return (
        <Box sx={{ p: 3 }}>
          {error ? <Alert severity="error">{error}</Alert> : <CircularProgress size={20} />}
        </Box>
      );
    }
    const blocked = detail.verdict === "block";
    const diff = detail.fix?.diff || detail.diff;
    const effect = APPROVAL_EFFECT(detail);
    const onDisk = effect === "accept";
    return (
      <Box sx={{ p: 3 }}>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }} flexWrap="wrap">
          <Typography variant="h6" sx={{ wordBreak: "break-all" }}>
            {detail.subject}
          </Typography>
          <Chip label={detail.verdict} size="small" color={VERDICT_COLOR[detail.verdict] || "default"} />
          <Chip label={detail.source} size="small" variant="outlined" />
          {detail.fix && <Chip label={`pending fix #${detail.fix.id}`} size="small" variant="outlined" />}
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 2 }}>
          {detail.files} file(s), +{detail.added_lines} line(s) · {formatWhen(detail.created_at)} · digest {detail.digest}
        </Typography>

        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          What the guard found
        </Typography>
        <FindingList findings={detail.findings} />

        {diff && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              The change
            </Typography>
            <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1, overflow: "hidden" }}>
              <SyntaxHighlighter
                language="diff"
                style={a11yDark}
                customStyle={{ margin: 0, fontSize: "0.75rem", maxHeight: 360 }}
                wrapLongLines
              >
                {diff}
              </SyntaxHighlighter>
            </Box>
          </Box>
        )}
        {onDisk && (
          <Alert severity="info" sx={{ mt: 2 }}>
            This change is already on disk; the source watch found it. Approving accepts the file as it is now.
            Rejecting leaves it there and keeps it out of what this machine sends to others until it changes.
          </Alert>
        )}
        {effect === "allow-again" && (
          <Alert severity="info" sx={{ mt: 2 }}>
            Nothing was kept to apply: this change was refused outright, so its text was not saved. Approving
            lets exactly this change through the next time it is made. Rejecting closes it.
          </Alert>
        )}

        <Divider sx={{ my: 2 }} />
        <TextField size="small" label="Note" value={note} onChange={(e) => setNote(e.target.value)} fullWidth />
        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}
        <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
          <Button
            variant="contained"
            color={blocked ? "error" : "primary"}
            startIcon={<CheckCircleIcon />}
            disabled={busy}
            onClick={() => setConfirming(true)}
          >
            {blocked
              ? "Approve despite the block"
              : onDisk
                ? "Accept"
                : effect === "allow-again"
                  ? "Approve"
                  : "Approve and apply"}
          </Button>
          <Button color="error" startIcon={<CancelIcon />} disabled={busy} onClick={() => act("reject")}>
            Reject
          </Button>
        </Stack>
      </Box>
    );
  };

  return (
    <>
      <Box sx={{ display: "flex", minHeight: 360 }}>
        <Box sx={{ width: 340, borderRight: 1, borderColor: "divider" }}>{renderList()}</Box>
        <Box sx={{ flex: 1, overflowY: "auto" }}>{renderDetail()}</Box>
      </Box>
      <ConfirmActionDialog
        open={confirming}
        title={detail?.verdict === "block" ? "Approve a blocked change?" : "Apply this change?"}
        description={
          APPROVAL_EFFECT(detail) === "allow-again"
            ? "Nothing is written now. Exactly this change will pass the guard the next time it is made; read every finding first."
            : detail?.verdict === "block"
              ? "The guard blocked this, not just held it. It lands only because you approve it here; read every finding first."
              : "The change is written into this checkout now. If the file no longer reads as it did when it was held, nothing is written and the reason is shown."
        }
        facts={detail ? [{ label: "Findings", value: detail.findings?.length || 0 }] : []}
        confirmLabel={detail?.verdict === "block" ? "Approve despite the block" : "Approve and apply"}
        busy={busy}
        onConfirm={() => act("approve")}
        onClose={() => setConfirming(false)}
      />
    </>
  );
};

export default HeldChangesPanel;
