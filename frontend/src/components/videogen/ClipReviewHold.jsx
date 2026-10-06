// frontend/src/components/videogen/ClipReviewHold.jsx
// A Video Gen clip that failed a quality check is held for a person (backend
// video_consistency_metrics.QUALITY_FLAG_OUTCOME): it says why, nothing
// downstream uses it, and it moves on only when someone approves it or asks
// for a re-render. Nothing re-renders on its own.

import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import { ActionButton, StatusPill } from "../settings/ui";

/** The card's state pill: "Needs review" / "Approved" / "Re-rendered", or null. */
export const ReviewStatePill = ({ review }) => {
  switch (review?.state) {
    case "needs_review":
      return <StatusPill tone="warn" label="Needs review" tooltip={(review.reasons || []).join(" · ")} />;
    case "approved":
      return <StatusPill tone="ok" label="Approved" tooltip={(review.reasons || []).join(" · ")} />;
    case "rerendered":
      return <StatusPill label="Re-rendered" tooltip={`New batch ${review.rerender_batch_id || ""}`} />;
    default:
      return null;
  }
};

/**
 * @param {object}   review      a result's review record
 * @param {Function} onApprove   keep the clip as rendered
 * @param {Function} onRerender  render it again as a new batch
 * @param {boolean}  [busy]
 * @param {string}   [heldText]  what waits on the person's answer
 */
const ClipReviewHold = ({
  review, onApprove, onRerender, busy = false,
  heldText = "It is not added to Files until you approve it.",
}) => {
  if (review?.state !== "needs_review") return null;
  const reasons = (review.reasons || []).join("; ");
  return (
    <Box sx={{ mt: 1 }}>
      <Typography variant="caption" color="warning.main" sx={{ display: "block" }}>
        Held for review{reasons ? `: ${reasons}` : ""}. {heldText}
      </Typography>
      <Stack direction="row" spacing={1} sx={{ mt: 0.75 }}>
        <ActionButton onClick={onApprove} loading={busy}>Approve</ActionButton>
        <ActionButton onClick={onRerender} disabled={busy}
          tooltip="Render this clip again as a new batch, same settings, new seed">
          Re-render
        </ActionButton>
      </Stack>
    </Box>
  );
};

export default ClipReviewHold;
