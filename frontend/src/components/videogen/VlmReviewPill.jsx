// frontend/src/components/videogen/VlmReviewPill.jsx
// The vision model's review of a Video Gen clip (backend
// video_consistency_metrics.review_video_quality): its 0-10 score, or
// "Not reviewed" with the reason when no score came back. A missing review
// must not look like a clean one, so it is shown rather than left out.

import React from "react";
import { StatusPill } from "../settings/ui";

/**
 * @param {object} review  a result's metadata.quality.vlm_review
 */
const VlmReviewPill = ({ review }) => {
  if (!review || typeof review !== "object") return null;
  const score = review.review?.quality_score;
  const reviewed = review.status ? review.status === "reviewed" : review.available === true;
  if (reviewed && typeof score === "number") {
    return (
      <StatusPill
        tone={score >= 5 ? "ok" : "warn"}
        label={`QA ${score}/10`}
        tooltip={review.review?.justification || ""}
      />
    );
  }
  // Records written before the "not reviewed" state carry only a reason code.
  const why = review.message || review.reason || "the vision review gave no score";
  return <StatusPill tone="neutral" label="Not reviewed" tooltip={`Not reviewed: ${why}`} />;
};

export default VlmReviewPill;
