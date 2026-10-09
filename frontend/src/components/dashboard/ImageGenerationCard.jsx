// frontend/src/components/dashboard/ImageGenerationCard.jsx
// Image Generation Dashboard Card - Quick access to image generation functionality

import React from "react";
import { Box, Button, IconButton, Tooltip } from "@mui/material";
import { Add, PlayArrow, Refresh } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import DashboardCardWrapper from "./DashboardCardWrapper";
import RecentRunsList from "./RecentRunsList";
import { loadImageBatchRows, useRecentRuns } from "./recentRuns";

// The Image Gen tab; BatchImageGeneratorPage reads ?mode= to pick its input mode.
export const IMAGE_GEN_PATH = "/batch-images";
export const IMAGE_GEN_BATCH_PATH = "/batch-images?mode=bulk";

const ImageGenerationCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      ...props
    },
    ref,
  ) => {
    const navigate = useNavigate();
    const recent = useRecentRuns(loadImageBatchRows);

    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title="Image Generation"
        {...props}
        contextMenuActions={[
          { label: "New Images", onClick: () => navigate(IMAGE_GEN_PATH) },
          { label: "Batch Mode", onClick: () => navigate(IMAGE_GEN_BATCH_PATH) },
        ]}
      >
        {/* Quick Actions */}
        <Box sx={{ mb: 2, display: "flex", gap: 1, flexWrap: "wrap" }}>
          <Button
            variant="contained"
            size="small"
            startIcon={<Add />}
            onClick={() => navigate(IMAGE_GEN_PATH)}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            New Images
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<PlayArrow />}
            onClick={() => navigate(IMAGE_GEN_BATCH_PATH)}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            Batch Mode
          </Button>
          <Tooltip title="Refresh data">
            <IconButton
              size="small"
              onClick={recent.refresh}
              className="non-draggable"
            >
              <Refresh fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>

        <RecentRunsList
          rows={recent.rows}
          loading={recent.loading}
          error={recent.error}
          emptyText="No image batches yet."
          onOpen={navigate}
          viewAll={{ label: "View all batches", path: IMAGE_GEN_PATH }}
        />
      </DashboardCardWrapper>
    );
  },
);

ImageGenerationCard.displayName = "ImageGenerationCard";
export default ImageGenerationCard;
