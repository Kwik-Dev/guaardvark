// Build from folder: turn a folder of your own notes into a training dataset.
// Check first (what is read and what is scrubbed, then a timed sample of three
// passages), and only then Build. Optionally also copies the folder into
// Files and indexes it, which is what lets questions about exact facts be
// answered from the notes themselves.
import React, { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  LinearProgress,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import DirectoryPicker from "../common/DirectoryPicker";
import { ActionButton, SettingChip, StatusPill } from "../settings/ui";
import {
  checkFolderBuild,
  getScrubTerms,
  probeFolderBuild,
  saveScrubTerms,
  startFolderBuild,
} from "../../api/trainingService";
import { startBulkImport } from "../../api/bulkImportService";

const KINDS = [
  { ext: ".md", label: "Markdown (.md)" },
  { ext: ".txt", label: "Text (.txt)" },
  { ext: ".html", label: "Web pages (.html)" },
  { ext: ".docx", label: "Word (.docx)" },
  { ext: ".pdf", label: "PDF (.pdf)" },
];
const PAIR_RATES = [1, 2, 3, 5, 8];
const RULE_LABELS = {
  private_key: "Private keys",
  api_key: "API keys and tokens",
  recovery_key: "Recovery keys",
  recovery_code: "Recovery codes",
  bearer_header: "Bearer tokens",
  api_key_header: "X-API-Key headers",
  secret_assignment: "Passwords and secrets",
  email: "Email addresses",
  private_ip: "Private network addresses",
  lan_host: "Local machine names",
  word_list: "Words from your list",
};

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const folderName = (path) => String(path || "").replace(/\/+$/, "").split("/").pop() || "Notes";

const formatDuration = (seconds) => {
  if (seconds == null) return null;
  if (seconds < 90) return `about ${Math.max(1, Math.round(seconds))} seconds`;
  if (seconds < 5400) return `about ${Math.round(seconds / 60)} minutes`;
  return `about ${(seconds / 3600).toFixed(1)} hours`;
};

const skippedLine = (skipped) => {
  if (!skipped) return "";
  const folders = (skipped.hidden_dirs || 0) + (skipped.internal_dirs || 0);
  const files = (skipped.hidden_files || 0) + (skipped.sensitive_files || 0) + (skipped.symlinks || 0);
  const parts = [];
  if (folders) parts.push(plural(folders, "hidden or internal folder"));
  if (files) parts.push(plural(files, "hidden or credential file"));
  if (skipped.package_files) {
    parts.push(`${plural(skipped.package_files, "file")} in ${plural(skipped.package_dirs || 0, "copied software folder")}`);
  }
  if (skipped.not_notes) parts.push(plural(skipped.not_notes, "licence, dependency or tokenizer file"));
  if (skipped.not_prose) parts.push(plural(skipped.not_prose, "file") + " that are not prose");
  if (skipped.credential_names) parts.push(plural(skipped.credential_names, "file") + " named for a credential");
  if (skipped.other_kinds) parts.push(plural(skipped.other_kinds, "file") + " of other kinds");
  return parts.length ? `Skipped ${parts.join(", ")}.` : "";
};

const BuildFromFolderModal = ({ open, onClose, onStarted }) => {
  const [folder, setFolder] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [kinds, setKinds] = useState([".md", ".txt"]);
  const [rate, setRate] = useState(3);
  const [alsoIndex, setAlsoIndex] = useState(true);
  const [terms, setTerms] = useState("");
  const [check, setCheck] = useState(null);
  const [probe, setProbe] = useState(null);
  const [estimate, setEstimate] = useState(null);
  const [busy, setBusy] = useState(null); // "check" | "probe" | "build"
  const [error, setError] = useState(null);
  const [probeError, setProbeError] = useState(null);
  const [showSamples, setShowSamples] = useState(false);

  useEffect(() => {
    if (!open) return;
    setCheck(null);
    setProbe(null);
    setEstimate(null);
    setError(null);
    setProbeError(null);
    getScrubTerms()
      .then((list) => setTerms(list.join("\n")))
      .catch(() => setTerms(""));
  }, [open]);

  // Any change to what would be read makes the last check stale.
  const invalidate = () => {
    setCheck(null);
    setProbe(null);
    setEstimate(null);
    setProbeError(null);
  };

  const body = () => ({ folder, kinds, pairs_per_1000_words: rate });

  const runCheck = async () => {
    setError(null);
    setProbeError(null);
    setBusy("check");
    try {
      await saveScrubTerms(terms.split("\n"));
      const result = await checkFolderBuild(body());
      setCheck(result);
      setEstimate(result.estimate);
      setProbe(null);
      if (result.files > 0) {
        setBusy("probe");
        try {
          const timed = await probeFolderBuild(body());
          setProbe(timed.probe);
          setEstimate(timed.estimate);
        } catch (e) {
          setProbeError(e.message || "Could not time the build");
        }
      }
    } catch (e) {
      setError(e.message || "The check failed");
    } finally {
      setBusy(null);
    }
  };

  const runBuild = async () => {
    setError(null);
    setBusy("build");
    try {
      const job = await startFolderBuild(body());
      let indexNote = "";
      if (alsoIndex) {
        try {
          await startBulkImport({
            source_path: folder,
            target_folder: folderName(folder),
            force_copy: true,
            reindex_missing: true,
            dry_run: false,
          });
          indexNote = ` The folder is also being copied into Files › ${folderName(folder)} and indexed.`;
        } catch (e) {
          indexNote = ` Adding it to Files did not start: ${e.message}`;
        }
      }
      onStarted?.(job, indexNote);
      onClose();
    } catch (e) {
      setError(e.message || "The build did not start");
    } finally {
      setBusy(null);
    }
  };

  const scrub = check?.scrub;
  const rulesHit = scrub ? Object.entries(scrub.by_rule || {}).filter(([, n]) => n > 0) : [];
  const nothingToRead = check && check.files === 0;
  const modelMissing = Boolean(probeError);

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="md" fullWidth>
      <DialogTitle>Build a dataset from a folder of notes</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          <Typography variant="body2" color="text.secondary">
            Your notes are read, scrubbed of secrets and names, cut into passages, and the local model writes
            questions and answers from each passage. Training on them teaches a small model how your notes talk:
            their words, decisions and the kind of answer you expect. It does not reliably recall exact facts,
            dates or numbers; searching your files does that, so adding the folder to Files is on.
          </Typography>

          <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ xs: "stretch", sm: "center" }}>
            <TextField
              size="small"
              label="Folder"
              value={folder}
              onChange={(e) => {
                setFolder(e.target.value);
                invalidate();
              }}
              placeholder="Choose a folder on the Guaardvark machine"
              sx={{ flexGrow: 1 }}
              disabled={Boolean(busy)}
            />
            <ActionButton onClick={() => setPickerOpen(true)} disabled={Boolean(busy)}>
              Browse…
            </ActionButton>
          </Stack>

          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
            <Typography variant="overline" color="text.secondary">
              Read
            </Typography>
            {KINDS.map((k) => (
              <SettingChip
                key={k.ext}
                label={k.label}
                on={kinds.includes(k.ext)}
                disabled={Boolean(busy)}
                onToggle={(on) => {
                  setKinds((prev) => (on ? [...prev, k.ext] : prev.filter((x) => x !== k.ext)));
                  invalidate();
                }}
              />
            ))}
          </Stack>

          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ xs: "stretch", sm: "center" }}>
            <TextField
              select
              size="small"
              label="Pairs per 1000 words"
              value={rate}
              onChange={(e) => {
                setRate(Number(e.target.value));
                invalidate();
              }}
              disabled={Boolean(busy)}
              sx={{ minWidth: 200 }}
            >
              {PAIR_RATES.map((n) => (
                <MenuItem key={n} value={n}>
                  {n}
                  {n === 3 ? " (default)" : ""}
                </MenuItem>
              ))}
            </TextField>
            <SettingChip
              label="Also add to Files and index it for search"
              on={alsoIndex}
              onToggle={setAlsoIndex}
              disabled={Boolean(busy)}
              tooltip="Copies the folder into Files (your originals stay where they are) and indexes it, so chat can answer from the notes themselves. Files keeps the notes as they are; only the training data is scrubbed."
            />
          </Stack>

          <TextField
            label="Words to scrub (one per line)"
            helperText="Names of people, machines and clients. They become [name] in the training data. Saved on this machine when you press Check."
            value={terms}
            onChange={(e) => {
              setTerms(e.target.value);
              invalidate();
            }}
            multiline
            minRows={3}
            maxRows={8}
            size="small"
            disabled={Boolean(busy)}
          />
          <Typography variant="caption" color="text.secondary">
            Always scrubbed as well: API keys and tokens, passwords and secret settings, email addresses, private
            network addresses and local machine names.
          </Typography>

          {busy && (busy === "check" || busy === "probe") && (
            <Box>
              <LinearProgress />
              <Typography variant="caption" color="text.secondary">
                {busy === "check" ? "Reading and scrubbing the folder…" : "Timing three passages with the local model…"}
              </Typography>
            </Box>
          )}
          {error && <Alert severity="error">{error}</Alert>}

          {check && (
            <Box data-testid="build-check">
              <Divider sx={{ mb: 1.5 }} />
              <Stack spacing={1}>
                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                  <StatusPill tone={nothingToRead ? "warn" : "ok"} label={nothingToRead ? "Nothing to read" : "Checked"} />
                  <Typography variant="body2">
                    {plural(check.files, "file")}, {check.words.toLocaleString()} words
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {skippedLine(check.skipped)}
                  </Typography>
                </Stack>
                {check.skipped?.credential_files?.length > 0 && (
                  <Alert severity="info">
                    Left out because their names say they hold a credential:{" "}
                    {check.skipped.credential_files.join(", ")}
                  </Alert>
                )}
                {check.unreadable?.length > 0 && (
                  <Alert severity="warning">Could not read {plural(check.unreadable.length, "file")}: {check.unreadable[0]}</Alert>
                )}
                <Typography variant="subtitle2">Scrubbed: {plural(scrub?.total || 0, "item")}</Typography>
                {rulesHit.length > 0 && (
                  <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                    {rulesHit.map(([rule, n]) => (
                      <StatusPill key={rule} tone="info" label={`${RULE_LABELS[rule] || rule}: ${n}`} />
                    ))}
                  </Stack>
                )}
                {scrub?.samples && Object.keys(scrub.samples).length > 0 && (
                  <Box>
                    <ActionButton kind="link" onClick={() => setShowSamples((v) => !v)}>
                      {showSamples ? "Hide scrubbed examples" : "Show scrubbed examples"}
                    </ActionButton>
                    <Collapse in={showSamples} unmountOnExit>
                      <Box sx={{ mt: 0.5, pl: 1, borderLeft: 2, borderColor: "divider" }}>
                        {Object.entries(scrub.samples).map(([rule, lines]) => (
                          <Box key={rule} sx={{ mb: 1 }}>
                            <Typography variant="caption" color="text.secondary">
                              {RULE_LABELS[rule] || rule}
                            </Typography>
                            {lines.map((line, i) => (
                              <Typography key={i} variant="body2" sx={{ fontFamily: "monospace", fontSize: "0.75rem", overflowWrap: "anywhere" }}>
                                {line}
                              </Typography>
                            ))}
                          </Box>
                        ))}
                      </Box>
                    </Collapse>
                  </Box>
                )}
                {estimate && !nothingToRead && (
                  <Typography variant="body2">
                    Estimate: {plural(estimate.chunks, "passage")}, about {estimate.pairs_before_filter} pairs before
                    filtering
                    {estimate.seconds != null
                      ? `, ${formatDuration(estimate.seconds)} on ${probe?.model || "the local model"}`
                      : ""}
                    .
                    <Typography component="span" variant="caption" color="text.secondary">
                      {" "}
                      {probe
                        ? `Timed on ${plural(probe.chunks, "passage")} (${probe.seconds_per_chunk} s each); an estimate.`
                        : `Chunk size ${estimate.basis}.`}
                    </Typography>
                  </Typography>
                )}
                {probeError && (
                  <Alert severity="warning">
                    {probeError}. The build needs the local chat model; start it from the chat model list, then Check
                    again.
                  </Alert>
                )}
              </Stack>
            </Box>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={onClose} disabled={busy === "build"}>
          Cancel
        </ActionButton>
        <ActionButton
          onClick={runCheck}
          loading={busy === "check" || busy === "probe"}
          disabled={!folder.trim() || kinds.length === 0 || Boolean(busy)}
        >
          Check
        </ActionButton>
        <ActionButton
          kind="primary"
          onClick={runBuild}
          loading={busy === "build"}
          disabled={!check || nothingToRead || modelMissing || Boolean(busy)}
          tooltip={!check ? "Check the folder first" : ""}
        >
          Build
        </ActionButton>
      </DialogActions>

      <DirectoryPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onSelect={(path) => {
          setFolder(path);
          invalidate();
        }}
        initialPath={folder || "~"}
        title="Choose a folder of notes"
        purpose="training"
      />
    </Dialog>
  );
};

export default BuildFromFolderModal;
