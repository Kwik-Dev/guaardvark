import React, { useEffect, useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  IconButton,
  Typography,
  Box,
  Chip,
  CircularProgress,
  Alert,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { listChatSessions, deleteChatSession } from "../../api/chatService";
import EntityContextMenu from "../common/EntityContextMenu";
import useContextMenu from "../../hooks/useContextMenu";
import { formatUiError } from "../../utils/uiError";

const PreviousChatsModal = ({ open, onClose, projectId, currentSessionId, onSelectSession }) => {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(false);
  // A failed load or delete, said in the dialog instead of an empty list.
  const [failure, setFailure] = useState(null);
  const rowMenu = useContextMenu();
  useEffect(() => {
    if (open) {
      loadSessions();
    }
  }, [open, projectId]);

  const loadSessions = async () => {
    setLoading(true);
    setFailure(null);
    try {
      const data = await listChatSessions(projectId);
      setSessions(data.sessions || []);
    } catch (err) {
      console.error("Failed to load sessions:", err);
      setFailure({ load: true, text: `Previous chats could not be loaded: ${formatUiError(err) || "the request failed"}.` });
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (sessionId, e) => {
    e?.stopPropagation();
    try {
      await deleteChatSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
      if (sessionId === currentSessionId) {
        onSelectSession(null); // Signal to start a new chat
      }
    } catch (err) {
      console.error("Failed to delete session:", err);
      setFailure({ load: false, text: `The chat was not deleted: ${formatUiError(err) || "the request failed"}.` });
    }
  };

  const formatDate = (isoString) => {
    if (!isoString) return "";
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now - date;
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays === 0) {
      return "Today " + date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } else if (diffDays === 1) {
      return "Yesterday " + date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } else if (diffDays < 7) {
      return date.toLocaleDateString([], { weekday: "short" }) + " " +
        date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    return date.toLocaleDateString([], { month: "short", day: "numeric" }) + " " +
      date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Previous Chats</DialogTitle>
      <DialogContent dividers sx={{ p: 0, height: 400, overflowY: "auto", display: "flex", flexDirection: "column", justifyContent: "flex-start" }}>
        {failure && (
          <Alert
            severity="error"
            sx={{ m: 1 }}
            onClose={failure.load ? undefined : () => setFailure(null)}
            action={failure.load ? <Button color="inherit" size="small" onClick={loadSessions}>Retry</Button> : undefined}
          >
            {failure.text}
          </Alert>
        )}
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress size={32} />
          </Box>
        ) : failure?.load ? null : sessions.length === 0 ? (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <Typography color="text.secondary">No previous chats</Typography>
          </Box>
        ) : (
          <List disablePadding>
            {sessions.map((session) => (
              <ListItem
                key={session.session_id}
                disablePadding
                onContextMenu={(e) => rowMenu.open(e, session)}
                secondaryAction={
                  <IconButton
                    edge="end"
                    size="small"
                    onClick={(e) => handleDelete(session.session_id, e)}
                    title="Remove chat"
                    sx={{
                      color: "text.disabled",
                      "&:hover": { color: "text.secondary" },
                    }}
                  >
                    <CloseIcon fontSize="small" />
                  </IconButton>
                }
              >
                <ListItemButton
                  selected={session.session_id === currentSessionId}
                  onClick={() => {
                    onSelectSession(session.session_id);
                    onClose();
                  }}
                >
                  <ListItemText
                    primary={session.preview || "New chat"}
                    secondary={formatDate(session.created_at)}
                    primaryTypographyProps={{
                      noWrap: true,
                      sx: { maxWidth: "85%", fontSize: "0.9rem" },
                    }}
                  />
                  <Chip
                    label={session.message_count}
                    size="small"
                    variant="outlined"
                    sx={{ ml: 1, minWidth: 32 }}
                  />
                </ListItemButton>
              </ListItem>
            ))}
          </List>
        )}
        <EntityContextMenu
          anchorPosition={rowMenu.anchorPosition}
          onClose={rowMenu.close}
          actions={
            rowMenu.payload
              ? [
                  {
                    label: "Open",
                    onClick: () => {
                      onSelectSession(rowMenu.payload.session_id);
                      onClose();
                    },
                  },
                  {
                    label: "Delete",
                    onClick: () => handleDelete(rowMenu.payload.session_id),
                    color: "error.main",
                    dividerBefore: true,
                  },
                ]
              : []
          }
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
};

export default PreviousChatsModal;
