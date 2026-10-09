// frontend/src/components/dashboard/CodeGenerationCard.jsx
// Code Generation Dashboard Card - Quick access to code generation functionality

import React from "react";
import { Box, Button, IconButton, Tooltip } from "@mui/material";
import { Add, PlayArrow, Refresh } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import DashboardCardWrapper from "./DashboardCardWrapper";
import RecentRunsList from "./RecentRunsList";
import { loadJobRows, useRecentRuns } from "./recentRuns";

const loadCodeJobs = () => loadJobRows("code_generation");

const CodeGenerationCard = React.forwardRef(
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
    const recent = useRecentRuns(loadCodeJobs);

    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title="Code Generation"
        {...props}
        contextMenuActions={[
          { label: "New Code", onClick: () => navigate("/code-editor") },
        ]}
      >
        {/* Quick Actions */}
        <Box sx={{ mb: 2, display: "flex", gap: 1, flexWrap: "wrap" }}>
          <Button
            variant="contained"
            size="small"
            startIcon={<Add />}
            onClick={() => navigate("/code-editor")}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            New Code
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<PlayArrow />}
            onClick={() => navigate("/code-editor")}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            Debug
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
          emptyText="No code generation jobs yet."
          onOpen={navigate}
          viewAll={{ label: "View all jobs", path: "/tasks" }}
        />
      </DashboardCardWrapper>
    );
  },
);

CodeGenerationCard.displayName = "CodeGenerationCard";
export default CodeGenerationCard;
