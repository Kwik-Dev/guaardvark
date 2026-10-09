// Tools chat runs without asking, chosen with "Always approve" on a tool card
// or "Always create" on the file card. "Ask again" brings the card back.
import React, { useCallback, useEffect, useState } from "react";
import { Alert, Box, CircularProgress, Stack, Typography } from "@mui/material";
import { ActionButton } from "../settings/ui";
import {
  getAlwaysApprovedTools,
  updateAlwaysApprovedTools,
} from "../../api/settingsService";

const AlwaysApprovedToolsPanel = ({ showMessage }) => {
  const [tools, setTools] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      setTools(await getAlwaysApprovedTools());
      setError(null);
    } catch (err) {
      setError(err.message || "Could not load the list");
      setTools([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const askAgain = async (name) => {
    setBusy(name);
    try {
      setTools(await updateAlwaysApprovedTools({ remove: [name] }));
      showMessage?.(`${name} asks before it runs again`);
    } catch (err) {
      setError(err.message || "Could not update the list");
    } finally {
      setBusy(null);
    }
  };

  if (tools === null) {
    return (
      <Box sx={{ p: 3, display: "flex", justifyContent: "center" }}>
        <CircularProgress size={24} />
      </Box>
    );
  }

  return (
    <Box sx={{ p: 2 }}>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      {tools.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          Chat asks before every tool that needs approval. Choosing "Always approve" on a
          tool card, or "Always create" on a file card, lists the tool here.
        </Typography>
      ) : (
        <Stack spacing={1}>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>
            Chat runs these without asking.
          </Typography>
          {tools.map((name) => (
            <Box key={name} sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
              <Typography variant="body2" sx={{ fontFamily: "monospace", minWidth: 220 }}>
                {name}
              </Typography>
              <ActionButton kind="neutral" loading={busy === name} onClick={() => askAgain(name)}>
                Ask again
              </ActionButton>
            </Box>
          ))}
        </Stack>
      )}
    </Box>
  );
};

export default AlwaysApprovedToolsPanel;
