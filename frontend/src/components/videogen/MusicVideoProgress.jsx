import React from "react";
import {
  Box,
  Chip,
  LinearProgress,
  Stack,
  Step,
  StepLabel,
  Stepper,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  CLIP_CELL_COLOR,
  STAGE_STATE_COLOR,
  STAGE_STATE_TEXT,
  activeStepIndex,
  clipCellState,
  currentClipText,
  etaText,
  formatDuration,
  visibleStages,
} from "./musicVideoProgressModel";

/**
 * Whole-run progress for a music video: a stage stepper, the overall bar with
 * an ETA, and one cell per cut. Reads `progress` from the music-video API
 * (backend/services/music_video_progress.py). `compact` draws only the bar and
 * one line, for list rows and project cards.
 */
export default function MusicVideoProgress({ progress, clips = [], compact = false, onCutClick }) {
  if (!progress || !Array.isArray(progress.stages) || progress.overall_pct == null) return null;
  const stages = visibleStages(progress);
  const active = stages.find((s) => ["active", "queued", "waiting", "failed", "cancelled"].includes(s.state));
  const failed = stages.some((s) => s.state === "failed");
  const pct = Number(progress.overall_pct) || 0;
  const eta = etaText(progress);
  const clipLine = currentClipText(progress);
  const barColor = failed ? "error" : active && active.state === "queued" ? "warning" : "primary";

  if (compact) {
    return (
      <Box sx={{ minWidth: 120 }} data-testid="mv-progress-compact">
        <LinearProgress variant="determinate" value={pct} color={barColor} sx={{ height: 4, borderRadius: 1 }} />
        <Typography variant="caption" color="text.secondary" noWrap display="block">
          {Math.round(pct)}%{active ? ` · ${active.message || active.label}` : ""}
        </Typography>
      </Box>
    );
  }

  const currentIndex = progress.current_clip ? progress.current_clip.index : null;
  const activeClips = (clips || []).filter((c) => c && c.status !== "cancelled");

  return (
    <Stack spacing={1.5} data-testid="mv-progress">
      <Stepper activeStep={activeStepIndex(stages)} alternativeLabel>
        {stages.map((s) => (
          <Step key={s.key} completed={s.state === "done"}>
            <StepLabel
              error={s.state === "failed"}
              optional={
                <Typography variant="caption" color="text.secondary" display="block" align="center">
                  {s.state === "done" && s.duration_s != null
                    ? formatDuration(s.duration_s)
                    : ["active", "queued"].includes(s.state) && s.pct
                      ? `${Math.round(s.pct)}%`
                      : STAGE_STATE_TEXT[s.state] || ""}
                </Typography>
              }
            >
              {s.label}
            </StepLabel>
          </Step>
        ))}
      </Stepper>

      <Box>
        <Stack direction="row" justifyContent="space-between" alignItems="baseline" spacing={1}>
          <Typography variant="body2">
            <b>{Math.round(pct)}%</b>
            {active && (
              <>
                {" · "}
                {active.message || active.label}
              </>
            )}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {[eta, progress.elapsed_seconds != null ? `${formatDuration(progress.elapsed_seconds)} elapsed` : ""]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
        </Stack>
        <LinearProgress
          variant="determinate"
          value={pct}
          color={barColor}
          sx={{ height: 8, borderRadius: 1, mt: 0.5 }}
        />
        {active && active.state && active.state !== "active" && (
          <Chip
            size="small"
            sx={{ mt: 0.75 }}
            color={STAGE_STATE_COLOR[active.state] || "default"}
            label={STAGE_STATE_TEXT[active.state]}
          />
        )}
      </Box>

      {activeClips.length > 0 && (
        <Box>
          {clipLine && (
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 0.5 }}>
              {clipLine}
            </Typography>
          )}
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: "3px" }} data-testid="mv-clip-strip">
            {activeClips.map((c) => {
              const state = clipCellState(c, currentIndex);
              return (
                <Tooltip key={c.index} title={`Cut #${c.index} · ${state}${c.locked ? " · locked" : ""}`}>
                  <Box
                    role={onCutClick ? "button" : undefined}
                    aria-label={`Cut ${c.index} ${state}`}
                    onClick={onCutClick ? () => onCutClick(c.index) : undefined}
                    sx={{
                      width: 14,
                      height: 14,
                      borderRadius: 0.5,
                      bgcolor: CLIP_CELL_COLOR[state],
                      cursor: onCutClick ? "pointer" : "default",
                      outline: state === "rendering" ? "2px solid" : "none",
                      outlineColor: "info.light",
                      animation: state === "rendering" ? "mvPulse 1.2s ease-in-out infinite" : "none",
                      "@keyframes mvPulse": { "0%,100%": { opacity: 1 }, "50%": { opacity: 0.45 } },
                    }}
                  />
                </Tooltip>
              );
            })}
          </Box>
        </Box>
      )}
    </Stack>
  );
}
