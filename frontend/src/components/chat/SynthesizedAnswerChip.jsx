import React from "react";
import { alpha } from "@mui/material/styles";
import { SettingChip } from "../settings/ui";
import { SYNTHESIZED_LABEL, SYNTHESIZED_TOOLTIP } from "./synthesizedAnswer";

/**
 * Read-only kit chip. SettingChip is the state pill (no glyph); it is not
 * toggled here — the flag is a fact about the turn.
 */
const SynthesizedAnswerChip = () => (
  <SettingChip
    label={SYNTHESIZED_LABEL}
    on
    tooltip={SYNTHESIZED_TOOLTIP}
    testId="synthesized-chip"
    sx={(theme) => ({
      cursor: "default",
      pointerEvents: "none",
      "&:hover": { bgcolor: alpha(theme.palette.primary.main, 0.18) },
    })}
  />
);

export default SynthesizedAnswerChip;
