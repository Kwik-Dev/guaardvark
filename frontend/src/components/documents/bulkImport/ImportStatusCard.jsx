// The running or finished import, in the work area above the fold: a status
// pill, progress, the worker's message and the results in plain counts.
import React, { useState } from "react";
import { Box, Collapse, LinearProgress, Paper, Stack, Typography } from "@mui/material";
import { ActionButton, StatusPill } from "../../settings/ui";
import { isTerminalStatus, jobPill, resultLine } from "./importStatus";

const PREVIEW_NAMES_SHOWN = 200;

const ImportStatusCard = ({ job, onOpenFiles, onImportAnother, onStop }) => {
  const [showList, setShowList] = useState(false);
  if (!job) return null;

  const pill = jobPill(job);
  const done = isTerminalStatus(job.status);
  const progress = typeof job.progress === "number" ? job.progress : null;
  const line = resultLine(job.stats, { preview: job.preview });
  const previewNames = job.preview && Array.isArray(job.stats?.files) ? job.stats.files : [];
  const failures = job.failures || [];
  const listed = previewNames.length ? previewNames : failures;

  return (
    <Paper variant="outlined" sx={{ p: 2 }} data-testid="import-status">
      <Stack spacing={1.25}>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ xs: "flex-start", sm: "center" }}>
          <StatusPill
            label={pill.label}
            tone={pill.tone}
            tooltip={job.jobId ? `Job ${job.jobId}` : ""}
          />
          <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1, minWidth: 0, overflowWrap: "anywhere" }}>
            {job.sourceLabel}
            {job.intoLabel ? ` → ${job.intoLabel}` : ""}
          </Typography>
          {!done && onStop && (
            <ActionButton onClick={onStop} tooltip="Finishes the file in progress, then stops">
              Stop
            </ActionButton>
          )}
        </Stack>

        {!done && (
          <LinearProgress
            variant={progress == null ? "indeterminate" : "determinate"}
            value={progress ?? 0}
          />
        )}

        {job.message && (
          <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>
            {job.message}
          </Typography>
        )}
        {line && (
          <Typography variant="body2" color="text.secondary">
            {line}
          </Typography>
        )}
        {job.awayNote && (
          <Typography variant="caption" color="text.secondary">
            {job.awayNote}
          </Typography>
        )}

        {listed.length > 0 && (
          <Box>
            <ActionButton kind="link" onClick={() => setShowList((v) => !v)}>
              {showList
                ? "Hide the list"
                : previewNames.length
                ? `Show the ${previewNames.length} file${previewNames.length === 1 ? "" : "s"}`
                : `Show the ${failures.length} failed`}
            </ActionButton>
            <Collapse in={showList} unmountOnExit>
              <Box
                component="ul"
                sx={{ m: 0, mt: 0.5, pl: 3, maxHeight: 220, overflowY: "auto", fontSize: "0.8rem", color: "text.secondary" }}
              >
                {listed.slice(0, PREVIEW_NAMES_SHOWN).map((name, i) => (
                  <li key={`${i}-${name}`}>{name}</li>
                ))}
                {listed.length > PREVIEW_NAMES_SHOWN && (
                  <li>and {listed.length - PREVIEW_NAMES_SHOWN} more</li>
                )}
              </Box>
            </Collapse>
          </Box>
        )}

        {done && (onOpenFiles || onImportAnother) && (
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            {onOpenFiles && !job.preview && <ActionButton onClick={onOpenFiles}>Open Files</ActionButton>}
            {onImportAnother && <ActionButton onClick={onImportAnother}>Import another</ActionButton>}
          </Stack>
        )}
      </Stack>
    </Paper>
  );
};

export default ImportStatusCard;
