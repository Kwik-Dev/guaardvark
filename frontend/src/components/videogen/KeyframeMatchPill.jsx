// frontend/src/components/videogen/KeyframeMatchPill.jsx
// How much a cinematic clip's middle frame still shows what its keyframe showed
// (backend video_consistency_metrics.keyframe_match), by the image encoder
// installed in Settings > Generation > Cast identity check. Clips recorded
// without the encoder carry a colour match instead (ColourMatchPill).

import React from "react";
import { StatusPill } from "../settings/ui";

/**
 * @param {object} quality  a result's metadata.quality
 */
const KeyframeMatchPill = ({ quality }) => {
  const match = quality?.keyframe_match;
  if (typeof match?.score !== "number") return null;
  const where = typeof match.frame_index === "number"
    ? ` at frame ${match.frame_index} of ${match.frames}`
    : "";
  const low = typeof match.threshold === "number" && match.score < match.threshold;
  return (
    <StatusPill
      tone={low ? "warn" : "neutral"}
      label={`Keyframe ${Math.round(match.score * 100)}%`}
      tooltip={`Image-encoder match between the keyframe and the clip${where}. Reacts to what is in the shot changing, not only its colours${typeof match.threshold === "number" ? `; under ${Math.round(match.threshold * 100)}% the clip is held for a look` : ""}.`}
    />
  );
};

export default KeyframeMatchPill;
