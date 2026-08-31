// frontend/src/components/settings/ui/ChoiceChips.jsx
// One of N, immediate. Same pill as SettingChip so it still reads as state;
// the group is a radiogroup so exactly one is lit.

import React from "react";
import { Box, ButtonBase, Tooltip } from "@mui/material";
import { chipSx } from "./SettingChip";

/**
 * @param {Array<{value:string,label:string,tooltip?:string,disabled?:boolean}>} options
 * @param {string}   value
 * @param {function} onChange   called with the chosen value (never null)
 * @param {string}   [ariaLabel]
 */
const ChoiceChips = ({ options, value, onChange, ariaLabel, disabled = false }) => (
  <Box role="radiogroup" aria-label={ariaLabel} sx={{ display: "inline-flex", gap: 0.75, flexWrap: "wrap" }}>
    {options.map((opt) => {
      const on = opt.value === value;
      const isDisabled = disabled || opt.disabled;
      const chip = (
        <ButtonBase
          key={opt.value}
          role="radio"
          aria-checked={on}
          disabled={isDisabled}
          onClick={() => !on && !isDisabled && onChange?.(opt.value)}
          sx={(theme) => chipSx(theme, on, isDisabled)}
        >
          {opt.label}
        </ButtonBase>
      );
      return opt.tooltip ? (
        <Tooltip key={opt.value} title={opt.tooltip}>
          <span style={{ display: "inline-flex" }}>{chip}</span>
        </Tooltip>
      ) : (
        chip
      );
    })}
  </Box>
);

export default ChoiceChips;
