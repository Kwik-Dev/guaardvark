// Self-improvement: Guaardvark checks its own code and stages fixes for review.
// The switches, the self-check, pending fixes, the run log and the code keeps
// that autoresearch stages, in one place.
import React, { useCallback, useEffect, useState } from "react";
import { Paper, Stack, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import PageLayout from "../components/layout/PageLayout";
import { useSnackbar } from "../components/common/SnackbarProvider";
import { ActionButton } from "../components/settings/ui";
import SelfImprovementControls from "../components/selfImprovement/SelfImprovementControls";
import SelfImprovementRuns from "../components/selfImprovement/SelfImprovementRuns";
import CodeKeepsTable from "../components/selfImprovement/CodeKeepsTable";
import { ragAutoresearchService } from "../api/ragAutoresearchService";
import { selfImprovementService } from "../api/selfImprovementService";

const SelfImprovementPage = () => {
  const navigate = useNavigate();
  const { showMessage } = useSnackbar();
  const [codeKeeps, setCodeKeeps] = useState([]);

  const fetchCodeKeeps = useCallback(async () => {
    try {
      const data = await ragAutoresearchService.getStatus();
      setCodeKeeps(Array.isArray(data?.code_keeps) ? data.code_keeps : []);
    } catch {
      setCodeKeeps([]);
    }
  }, []);

  useEffect(() => {
    fetchCodeKeeps();
  }, [fetchCodeKeeps]);

  const handleCodeKeep = async (fixId, action) => {
    try {
      if (action === "approve") {
        await selfImprovementService.approveFix(fixId);
        showMessage("Code keep approved. Apply it from Pending fixes.", "success");
      } else {
        await selfImprovementService.rejectFix(fixId);
        showMessage("Code keep rejected", "info");
      }
      fetchCodeKeeps();
    } catch (e) {
      showMessage(`Failed to ${action} code keep: ${e.message}`, "error");
    }
  };

  return (
    <PageLayout
      title="Self-improvement"
      variant="standard"
      actions={
        <Stack direction="row" spacing={1} sx={{ display: { xs: "none", sm: "flex" } }}>
          <ActionButton kind="link" onClick={() => navigate("/system-map")}>
            System Map
          </ActionButton>
          <ActionButton kind="link" onClick={() => navigate("/settings")}>
            Uncle Claude in Settings
          </ActionButton>
        </Stack>
      }
    >
      <Stack spacing={2}>
        <Typography variant="body2" color="text.secondary">
          Guaardvark runs its own tests, drafts fixes and stages them for your review. Nothing reaches the
          code until you approve and apply it, and Codebase locked stops every change.
        </Typography>

        <Paper variant="outlined" sx={{ p: 2 }}>
          <SelfImprovementControls />
        </Paper>

        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="h6" sx={{ mb: 1 }}>
            Recent runs
          </Typography>
          <SelfImprovementRuns />
        </Paper>

        {codeKeeps.length > 0 ? (
          <CodeKeepsTable keeps={codeKeeps} onDecide={handleCodeKeep} />
        ) : (
          <Typography variant="body2" color="text.secondary">
            No code keeps from Autoresearch.
          </Typography>
        )}
      </Stack>
    </PageLayout>
  );
};

export default SelfImprovementPage;
