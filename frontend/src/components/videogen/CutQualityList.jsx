// frontend/src/components/videogen/CutQualityList.jsx
// The quality check of each rendered music-video cut (backend
// music_video_tasks._cut_quality): its flags, and for a cut the check held,
// the reason with Approve and Re-render. The video is assembled only once no
// cut is held.

import React from "react";
import { Box, Stack, Typography } from "@mui/material";
import QualityFlagsPill from "./QualityFlagsPill";
import VlmReviewPill from "./VlmReviewPill";
import ColourMatchPill from "./ColourMatchPill";
import ClipReviewHold, { ReviewStatePill } from "./ClipReviewHold";

/**
 * @param {object[]} clips       the music video's clips (index, status, quality, review)
 * @param {Function} onApprove   (index) => keep the held cut
 * @param {Function} onRerender  (index) => render it again
 * @param {boolean}  [busy]
 */
const CutQualityList = ({ clips, onApprove, onRerender, busy = false }) => {
  const checked = (clips || []).filter((c) => c && c.status === "done" && (c.quality || c.review));
  if (checked.length === 0) return null;
  const held = checked.filter((c) => c.review?.state === "needs_review");
  return (
    <Box sx={{ mb: 2 }}>
      <Typography variant="subtitle2" gutterBottom>
        Cut quality
        {held.length > 0 ? ` — ${held.length} held for review; the video is assembled once each is approved or re-rendered` : ""}
      </Typography>
      <Stack spacing={1}>
        {checked.map((c) => (
          <Box key={c.index}>
            <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap">
              <Typography variant="body2" sx={{ mr: 0.5 }}>Cut #{c.index}</Typography>
              <ReviewStatePill review={c.review} />
              <QualityFlagsPill quality={c.quality} />
              <ColourMatchPill quality={c.quality} />
              <VlmReviewPill review={c.quality?.vlm_review} />
              {!c.review && !(c.quality?.flags || []).length && (
                <Typography variant="caption" color="text.secondary">no problems found</Typography>
              )}
            </Stack>
            <ClipReviewHold
              review={c.review}
              busy={busy}
              onApprove={() => onApprove(c.index)}
              onRerender={() => onRerender(c.index)}
              heldText="It is not used in the video until you approve it."
            />
          </Box>
        ))}
      </Stack>
    </Box>
  );
};

export default CutQualityList;
