// Outreach drafts waiting for a person. Supervised mode (the default) keeps a
// graded draft here until someone approves it; approving hands it to the poster,
// which sends it on the next pass while outreach is switched on.
import React, { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Link,
  List,
  ListItemButton,
  ListItemText,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { Cancel as CancelIcon, CheckCircle as CheckCircleIcon } from "@mui/icons-material";
import { Link as RouterLink } from "react-router-dom";
import { approveDraft, rejectDraft } from "../../api/outreachService";

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

const OutreachDraftsPanel = ({ drafts, loading, onChanged, showMessage }) => {
  const [selectedId, setSelectedId] = useState(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const selected = (drafts || []).find((d) => d.id === selectedId) || null;

  // Reset the editable text only when another draft is picked, not on every poll.
  useEffect(() => {
    setText(selected?.draft_text || "");
    setError(null);
  }, [selectedId]);

  const act = async (approve) => {
    setBusy(true);
    setError(null);
    try {
      if (approve) {
        await approveDraft(selected.id, text !== selected.draft_text ? text : null);
        showMessage?.("Approved; it posts on the next pass while outreach is on");
      } else {
        await rejectDraft(selected.id);
        showMessage?.("Rejected; it will not post");
      }
      setSelectedId(null);
      await onChanged?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
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
    if (!drafts?.length) {
      return (
        <Alert severity="info" sx={{ m: 2 }}>
          No outreach drafts are waiting.
        </Alert>
      );
    }
    return (
      <List dense disablePadding sx={{ maxHeight: 520, overflowY: "auto" }}>
        {drafts.map((d) => (
          <ListItemButton key={d.id} selected={selectedId === d.id} onClick={() => setSelectedId(d.id)}>
            <ListItemText
              primary={
                <Stack direction="row" spacing={1} alignItems="center">
                  <Chip label={d.platform} size="small" variant="outlined" />
                  <Typography variant="body2" noWrap sx={{ minWidth: 0 }}>
                    {d.action}
                  </Typography>
                </Stack>
              }
              secondary={
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}
                >
                  {d.draft_text || "(no text)"} · {formatWhen(d.created_at)}
                </Typography>
              }
            />
          </ListItemButton>
        ))}
      </List>
    );
  };

  const renderDetail = () => {
    if (!selected) {
      return (
        <Typography variant="body2" color="text.secondary" sx={{ p: 3 }}>
          Select a draft to read and approve it.
        </Typography>
      );
    }
    return (
      <Box sx={{ p: 3 }}>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }} flexWrap="wrap">
          <Typography variant="h6">{selected.platform}</Typography>
          <Chip label={selected.action} size="small" variant="outlined" />
          {selected.grade_score != null && (
            <Chip label={`grade ${Number(selected.grade_score).toFixed(2)}`} size="small" variant="outlined" />
          )}
        </Stack>
        {selected.target_url && (
          <Typography variant="body2" sx={{ mb: 2, wordBreak: "break-all" }}>
            <Link href={selected.target_url} target="_blank" rel="noopener noreferrer">
              {selected.target_url}
            </Link>
          </Typography>
        )}
        <TextField
          label="What will be posted"
          value={text}
          onChange={(e) => setText(e.target.value)}
          multiline
          minRows={5}
          fullWidth
        />
        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}
        <Stack direction="row" spacing={1} sx={{ mt: 2 }} alignItems="center">
          <Button variant="contained" startIcon={<CheckCircleIcon />} disabled={busy || !text.trim()} onClick={() => act(true)}>
            {busy ? "Working…" : "Approve"}
          </Button>
          <Button color="error" startIcon={<CancelIcon />} disabled={busy} onClick={() => act(false)}>
            Reject
          </Button>
          <Box sx={{ flex: 1 }} />
          <Link component={RouterLink} to="/outreach" variant="body2">
            Outreach page
          </Link>
        </Stack>
      </Box>
    );
  };

  return (
    <Box sx={{ display: "flex", minHeight: 360 }}>
      <Box sx={{ width: 340, borderRight: 1, borderColor: "divider" }}>{renderList()}</Box>
      <Box sx={{ flex: 1, overflowY: "auto" }}>{renderDetail()}</Box>
    </Box>
  );
};

export default OutreachDraftsPanel;
