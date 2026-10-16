import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  MenuItem,
  Stack,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";

import {
  fetchConnections,
  fetchProviders,
  preflightPublish,
  queuePublish,
} from "../../api/connectionsService";

/** Debounce so preflight doesn't fire on every keystroke. */
const useDebounced = (value, delay = 400) => {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
};

/** A Date as the value a datetime-local input takes, in local time. */
const toLocalInput = (date) => {
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
};

/** The next whole hour at least an hour away: a sensible first suggestion. */
const suggestedTime = () => {
  const date = new Date(Date.now() + 60 * 60 * 1000);
  date.setMinutes(0, 0, 0);
  return toLocalInput(new Date(date.getTime() + 60 * 60 * 1000));
};

/**
 * Compose a post and publish selected assets to one or more connections.
 * Validation limits come from each provider's capabilities, never hardcoded;
 * the AI disclosure rule and who sends a scheduled post come back per
 * destination from preflight.
 */
const PublishModal = ({ open, onClose, documents = [], onFeedback }) => {
  const [connections, setConnections] = useState([]);
  const [specs, setSpecs] = useState({});
  const [selected, setSelected] = useState([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [tags, setTags] = useState("");
  const [visibility, setVisibility] = useState("");
  const [aiDisclosure, setAiDisclosure] = useState(false);
  const [scheduleOn, setScheduleOn] = useState(false);
  const [scheduledAt, setScheduledAt] = useState("");
  const [preflight, setPreflight] = useState(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const documentIds = useMemo(
    () => documents.map((d) => d.id).filter(Boolean),
    [documents],
  );

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([fetchConnections("social"), fetchProviders("social")])
      .then(([conns, providerSpecs]) => {
        if (cancelled) return;
        setConnections(conns);
        setSpecs(Object.fromEntries(providerSpecs.map((s) => [s.provider, s])));
      })
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const first = documents[0];
    setTitle(first?.filename || "");
    setBody(first?.metadata?.original_prompt || first?.summary || "");
    setTags(Array.isArray(first?.tags) ? first.tags.join(", ") : "");
    setSelected([]);
    setVisibility("");
    setAiDisclosure(false);
    setScheduleOn(false);
    setScheduledAt("");
    setPreflight(null);
    setError(null);
  }, [open, documents]);

  // ISO with the browser's offset, so the server reads the same moment.
  const scheduledIso = useMemo(() => {
    if (!scheduleOn || !scheduledAt) return null;
    const date = new Date(scheduledAt);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
  }, [scheduleOn, scheduledAt]);

  const selectedSpecs = useMemo(
    () =>
      selected
        .map((id) => connections.find((c) => c.id === id))
        .filter(Boolean)
        .map((c) => specs[c.provider])
        .filter(Boolean),
    [selected, connections, specs],
  );

  // The strictest limit across the selected targets governs the counter.
  const charLimit = useMemo(() => {
    const limits = selectedSpecs
      .map((s) => s.capabilities?.max_text_chars)
      .filter((n) => typeof n === "number" && n > 0);
    return limits.length ? Math.min(...limits) : null;
  }, [selectedSpecs]);

  const supportsTitle = selectedSpecs.some((s) => s.capabilities?.supports_title);
  const supportsTags = selectedSpecs.some((s) => s.capabilities?.supports_tags);

  const visibilityOptions = useMemo(() => {
    const sets = selectedSpecs
      .map((s) => s.capabilities?.visibilities || [])
      .filter((v) => v.length);
    if (!sets.length) return [];
    return sets.reduce((acc, cur) => acc.filter((v) => cur.includes(v)));
  }, [selectedSpecs]);

  const debouncedBody = useDebounced(body);
  const debouncedTitle = useDebounced(title);

  useEffect(() => {
    if (!open || !selected.length) {
      setPreflight(null);
      return;
    }
    let cancelled = false;
    preflightPublish({
      connection_ids: selected,
      document_ids: documentIds,
      body: debouncedBody,
      title: supportsTitle ? debouncedTitle : null,
      visibility: visibility || null,
      scheduled_at: scheduledIso,
      ai_disclosure: aiDisclosure,
    })
      .then((result) => !cancelled && setPreflight(result))
      .catch(() => !cancelled && setPreflight(null));
    return () => {
      cancelled = true;
    };
  }, [
    open, selected, documentIds, debouncedBody, debouncedTitle,
    visibility, supportsTitle, scheduledIso, aiDisclosure,
  ]);

  const toggle = useCallback((id) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  }, []);

  const violationsFor = useCallback(
    (id) => preflight?.per_connection?.[String(id)]?.violations || [],
    [preflight],
  );

  // Top-level violations apply to every destination, e.g. public posting stopped.
  const allViolations = useMemo(() => {
    if (!preflight) return [];
    const general = preflight.violations || [];
    const perConnection = Object.entries(preflight.per_connection || {}).flatMap(([id, entry]) =>
      (entry.violations || []).map((v) => `${entry.label || id}: ${v}`),
    );
    return [...general, ...perConnection];
  }, [preflight]);

  // Destinations whose terms require the AI label for this post: the switch
  // cannot turn it off there, and each one says why.
  const requiredDisclosures = useMemo(
    () =>
      Object.values(preflight?.per_connection || {})
        .filter((entry) => entry.ai_disclosure?.required)
        .map((entry) => ({ label: entry.label, rule: entry.ai_disclosure.rule })),
    [preflight],
  );

  const scheduleNotes = useMemo(
    () =>
      Object.values(preflight?.per_connection || {})
        .filter((entry) => entry.schedule?.note)
        .map((entry) => ({ label: entry.label, note: entry.schedule.note })),
    [preflight],
  );

  const handlePublish = useCallback(async () => {
    setSubmitting(true);
    setError(null);
    try {
      const result = await queuePublish({
        connection_ids: selected,
        document_ids: documentIds,
        body,
        title: supportsTitle ? title : null,
        tags: tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        visibility: visibility || null,
        requested_by: "ui",
        scheduled_at: scheduledIso,
        ai_disclosure: aiDisclosure,
      });
      const verb = scheduledIso ? "Scheduled" : "Queued";
      onFeedback?.({
        open: true,
        message: result.requires_approval
          ? `${verb} ${result.count} publish(es) — review them under Approvals.`
          : scheduledIso
            ? `Scheduled ${result.count} publish(es); see them under Social Posts.`
            : `Publishing to ${result.count} destination(s).`,
        severity: "success",
      });
      onClose?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  }, [
    selected, documentIds, body, title, tags, visibility, supportsTitle,
    scheduledIso, aiDisclosure, onClose, onFeedback,
  ]);

  const overLimit = charLimit !== null && body.length > charLimit;
  const canPublish =
    selected.length > 0 &&
    !overLimit &&
    !allViolations.length &&
    !submitting &&
    (!scheduleOn || Boolean(scheduledIso));

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        Publish {documents.length > 1 ? `${documents.length} items` : "asset"}
      </DialogTitle>
      <DialogContent dividers>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress size={28} />
          </Box>
        ) : (
          <Stack spacing={2} sx={{ mt: 1 }}>
            {error && <Alert severity="error">{error}</Alert>}

            {!connections.length && (
              <Alert severity="info">
                No social accounts yet. Add one on the Social Accounts page first.
              </Alert>
            )}

            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Destinations
              </Typography>
              <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                {connections.map((conn) => {
                  const problems = violationsFor(conn.id);
                  const isSelected = selected.includes(conn.id);
                  const chip = (
                    <Chip
                      key={conn.id}
                      label={conn.display_name || conn.handle || conn.provider}
                      color={
                        isSelected && problems.length
                          ? "error"
                          : isSelected
                            ? "primary"
                            : "default"
                      }
                      variant={isSelected ? "filled" : "outlined"}
                      onClick={() => toggle(conn.id)}
                      disabled={!conn.enabled || !conn.has_credentials}
                    />
                  );
                  const reason = !conn.has_credentials
                    ? "No credentials configured"
                    : !conn.enabled
                      ? "Connection disabled"
                      : problems[0];
                  return reason ? (
                    <Tooltip key={conn.id} title={reason}>
                      <span>{chip}</span>
                    </Tooltip>
                  ) : (
                    chip
                  );
                })}
              </Stack>
            </Box>

            <Divider />

            {supportsTitle && (
              <TextField
                label="Title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                size="small"
              />
            )}

            <TextField
              label="Caption"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              multiline
              minRows={3}
              size="small"
              error={overLimit}
              helperText={
                charLimit !== null
                  ? `${body.length} / ${charLimit} characters`
                  : `${body.length} characters`
              }
            />

            {supportsTags && (
              <TextField
                label="Tags"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                size="small"
                helperText="Comma separated."
              />
            )}

            {visibilityOptions.length > 0 && (
              <TextField
                select
                label="Visibility"
                value={visibility || visibilityOptions[0]}
                onChange={(e) => setVisibility(e.target.value)}
                size="small"
              >
                {visibilityOptions.map((option) => (
                  <MenuItem key={option} value={option}>
                    {option}
                    {option !== "private" && (
                      <WarningAmberIcon
                        fontSize="small"
                        color="warning"
                        sx={{ ml: 1, verticalAlign: "middle" }}
                      />
                    )}
                  </MenuItem>
                ))}
              </TextField>
            )}

            <Box>
              <FormControlLabel
                control={
                  <Switch
                    checked={aiDisclosure}
                    onChange={(e) => setAiDisclosure(e.target.checked)}
                    size="small"
                  />
                }
                label="Label as made with AI"
              />
              {requiredDisclosures.map((entry) => (
                <Typography
                  key={entry.label}
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block" }}
                >
                  Always on for {entry.label}: {entry.rule}
                </Typography>
              ))}
            </Box>

            <Box>
              <FormControlLabel
                control={
                  <Switch
                    checked={scheduleOn}
                    onChange={(e) => {
                      setScheduleOn(e.target.checked);
                      if (e.target.checked && !scheduledAt) setScheduledAt(suggestedTime());
                    }}
                    size="small"
                  />
                }
                label="Schedule for later"
              />
              {scheduleOn && (
                <Stack spacing={0.5} sx={{ mt: 1 }}>
                  <TextField
                    type="datetime-local"
                    label="Goes out at"
                    value={scheduledAt}
                    onChange={(e) => setScheduledAt(e.target.value)}
                    size="small"
                    InputLabelProps={{ shrink: true }}
                    helperText="Still waits for approval first when approval is required."
                  />
                  {scheduleNotes.map((entry) => (
                    <Typography key={entry.label} variant="caption" color="text.secondary">
                      {entry.label}: {entry.note}
                    </Typography>
                  ))}
                </Stack>
              )}
            </Box>

            {allViolations.length > 0 && (
              <Alert severity="warning">
                <Stack spacing={0.5}>
                  {allViolations.map((v) => (
                    <Typography key={v} variant="body2">
                      {v}
                    </Typography>
                  ))}
                </Stack>
              </Alert>
            )}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={handlePublish} disabled={!canPublish}>
          {submitting ? (scheduleOn ? "Scheduling…" : "Publishing…") : scheduleOn ? "Schedule" : "Publish"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default PublishModal;
