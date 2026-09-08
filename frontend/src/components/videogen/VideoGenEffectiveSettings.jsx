import React from "react";
import { Box, Chip, Typography } from "@mui/material";
import {
  isWanModel,
  MODEL_OPTIONS,
  modelChipLabel,
  PROMPT_STYLES,
} from "../../constants/videoGeneratorPresets";

/**
 * Compact "what will actually run" summary derived from computedParams.
 * Family chip is MODEL_OPTIONS[computedParams.model].type — never a CogVideoX default.
 */
export default function VideoGenEffectiveSettings({
  model,
  computedParams,
  cinematicKeyframe,
  selectedSubjectIds = [],
  keyframeModel,
  directorMode,
  faceRestore,
  freeu,
  capabilities = null,
}) {
  if (!computedParams) return null;
  const runningId = computedParams.model || model;
  const family = MODEL_OPTIONS[runningId]?.type;
  const familyColor =
    family === "ltx" ? "warning"
    : family === "minimax" ? "success"
    : family === "wan" ? "secondary"
    : family === "hunyuan" ? "info"
    : family === "cogvideox" ? "primary"
    : "default";
  return (
    <Box sx={{ mb: 2 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ mb: 1, display: "block", fontWeight: 500 }}
      >
        Effective settings
      </Typography>
      <Box
        sx={{
          display: "flex",
          gap: 1,
          flexWrap: "wrap",
          alignItems: "center",
          p: 1.5,
          borderRadius: 1,
          bgcolor: "action.hover",
        }}
      >
        <Chip
          size="small"
          color={familyColor}
          label={modelChipLabel(runningId)}
          data-testid="effective-model"
          data-model={runningId}
          title={MODEL_OPTIONS[runningId]?.label || runningId}
          sx={{ fontWeight: 600 }}
        />
        <Chip size="small" variant="outlined" label={`${computedParams.num_inference_steps} steps`} />
        {computedParams.speed_profile && computedParams.speed_profile !== "standard" && (
          <Chip
            size="small"
            variant="outlined"
            color="success"
            label={capabilities?.speed_profiles?.[computedParams.speed_profile]?.label || computedParams.speed_profile}
          />
        )}
        {capabilities?.audio_out && (
          <Chip size="small" variant="outlined" color="success" label="native audio" />
        )}
        {computedParams.style_embedding && (
          <Chip size="small" variant="outlined" label={`style: ${computedParams.style_embedding}`} />
        )}
        <Chip size="small" variant="outlined" label={`${computedParams.duration_frames} frames`} />
        <Chip size="small" variant="outlined" label={`${computedParams.fps} FPS`} />
        <Chip
          size="small"
          variant="outlined"
          label={`~${(computedParams.duration_frames / computedParams.fps).toFixed(1)}s`}
        />
        <Chip
          size="small"
          variant="outlined"
          label={`${computedParams.width}×${computedParams.height}`}
        />
        {(cinematicKeyframe || selectedSubjectIds.length > 0) && (
          <Chip size="small" variant="outlined" color="secondary" label={`Keyframe: ${keyframeModel}`} />
        )}
        {directorMode && (
          <Chip size="small" variant="outlined" color="secondary" label="Director" />
        )}
        {faceRestore && (
          <Chip size="small" variant="outlined" color="success" label="Face restore" />
        )}
        {freeu && isWanModel(model) && (
          <Chip size="small" variant="outlined" color="success" label="FreeU" />
        )}
        {computedParams.interpolation_multiplier > 1 && (
          <Chip
            size="small"
            variant="outlined"
            color="info"
            label={`${computedParams.interpolation_multiplier}x FPS`}
          />
        )}
        {computedParams.upscale && (
          <Chip size="small" variant="outlined" color="secondary" label="2x Upscale" />
        )}
        {computedParams.enhance_prompt && computedParams.prompt_style !== "none" && (
          <Chip
            size="small"
            variant="outlined"
            color="warning"
            label={`${PROMPT_STYLES[computedParams.prompt_style]?.label || computedParams.prompt_style} style`}
          />
        )}
        {computedParams.feta_weight && (
          <Chip
            size="small"
            variant="outlined"
            color="success"
            label={`FETA ${computedParams.feta_weight}`}
          />
        )}
        {(computedParams.subject_ids || []).length > 0 && (
          <Chip
            size="small"
            variant="outlined"
            color="secondary"
            label={`Cast ×${computedParams.subject_ids.length}`}
          />
        )}
      </Box>
    </Box>
  );
}
