// frontend/src/components/system/DiagnosticsPanel.jsx
// Self-contained system diagnostics + test-suite panel for the System Dashboard.
// Owns its own state and API calls; `showMessage` is optional feedback plumbing.

import React, { useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Chip,
  Collapse,
  Divider,
  Paper,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import MuiAlert from "@mui/material/Alert";
import { alpha } from "@mui/material/styles";

import ApiIcon from "@mui/icons-material/Api";
import ChatIcon from "@mui/icons-material/Chat";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import SystemIcon from "@mui/icons-material/Computer";
import DnsIcon from "@mui/icons-material/Dns";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import FolderIcon from "@mui/icons-material/Folder";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import SecurityIcon from "@mui/icons-material/Security";
import SpeedIcon from "@mui/icons-material/Speed";
import StorageIcon from "@mui/icons-material/Storage";
import SyncProblemIcon from "@mui/icons-material/SyncProblem";
import TrendingUpIcon from "@mui/icons-material/TrendingUp";
import WarningIcon from "@mui/icons-material/Warning";

import { runAllTests, runSelfTest } from "../../api/settingsService";

// Diagnostics depth is one choice; "Full" is the API's "comprehensive" mode.
const DEPTH_OPTIONS = [
  { value: "basic", label: "Basic", running: "basic system checks" },
  { value: "quick", label: "Quick", running: "quick validation" },
  { value: "comprehensive", label: "Full", running: "comprehensive testing" },
];

const depthLabel = (value) =>
  DEPTH_OPTIONS.find((o) => o.value === value)?.label || value;

// Helper function to get category icon
const getCategoryIcon = (categoryKey) => {
  const iconMap = {
    core_system: <SystemIcon />,
    api_health: <ApiIcon />,
    file_processing: <FolderIcon />,
    chat_system: <ChatIcon />,
    security: <SecurityIcon />,
    performance: <TrendingUpIcon />
  };
  return iconMap[categoryKey] || <HelpOutlineIcon />;
};

// Helper function to get status color and icon
const getStatusDisplay = (status) => {
  const statusMap = {
    pass: { color: "success", icon: <CheckCircleOutlineIcon />, label: "Pass" },
    fail: { color: "error", icon: <ErrorOutlineIcon />, label: "Failed" },
    warning: { color: "warning", icon: <WarningIcon />, label: "Warning" },
    error: { color: "error", icon: <SyncProblemIcon />, label: "Error" },
    critical: { color: "error", icon: <ErrorOutlineIcon />, label: "Critical" },
    partial: { color: "warning", icon: <WarningIcon />, label: "Partial" },
    skip: { color: "default", icon: <InfoOutlinedIcon />, label: "Skipped" },
  };
  const key = typeof status === "string" ? status.toLowerCase() : "";
  return statusMap[key] || { color: "default", icon: <InfoOutlinedIcon />, label: status || "Unknown" };
};

const renderStatusIcon = (value, detailsForKey = "") => {
  const details = String(detailsForKey).toLowerCase();

  if (typeof value === "boolean") {
    return value ? (
      <CheckCircleOutlineIcon
        sx={{ color: "success.main", verticalAlign: "middle" }}
      />
    ) : (
      <ErrorOutlineIcon
        sx={{ color: "error.main", verticalAlign: "middle" }}
      />
    );
  }
  if (typeof value === "string") {
    const lowerValue = value.toLowerCase();
    if (
      [
        "ok",
        "good",
        "healthy",
        "accessible",
        "loadable",
        "active",
        "true",
        "responsive",
        "idle / queue empty",
        "no recent indexing errors found in db",
        "no error/critical messages in last ~200 lines",
      ].some((s) => lowerValue.includes(s))
    ) {
      return (
        <CheckCircleOutlineIcon
          sx={{ color: "success.main", verticalAlign: "middle" }}
        />
      );
    }
    if (
      [
        "error",
        "failed",
        "unhealthy",
        "inaccessible",
        "critical",
        "false",
        "db error",
      ].some((s) => lowerValue.includes(s)) ||
      lowerValue.startsWith("error:") ||
      lowerValue.includes("error(s). examples:")
    ) {
      return (
        <ErrorOutlineIcon
          sx={{ color: "error.main", verticalAlign: "middle" }}
        />
      );
    }
    if (
      [
        "warning",
        "degraded",
        "not configured",
        "configured but not responsive/empty response",
        "items pending/indexing",
      ].some((s) => lowerValue.includes(s))
    ) {
      return (
        <SyncProblemIcon
          sx={{ color: "warning.main", verticalAlign: "middle" }}
        />
      );
    }
    if (lowerValue.includes("unknown") || lowerValue.includes("n/a")) {
      return (
        <HelpOutlineIcon
          sx={{ color: "text.secondary", verticalAlign: "middle" }}
        />
      );
    }
  }
  if (details) {
    if (
      ["ok", "found", "connected", "accessible", "responsive", "idle"].some(
        (s) => details.includes(s),
      )
    ) {
      return (
        <CheckCircleOutlineIcon
          sx={{ color: "success.main", verticalAlign: "middle" }}
        />
      );
    }
    if (
      [
        "failed",
        "error",
        "inaccessible",
        "not found/empty",
        "not found or not active for the current model",
      ].some((s) => details.includes(s))
    ) {
      return (
        <ErrorOutlineIcon
          sx={{ color: "error.main", verticalAlign: "middle" }}
        />
      );
    }
    if (
      details.includes("pending/indexing") ||
      details.includes("not configured")
    ) {
      return (
        <SyncProblemIcon
          sx={{ color: "warning.main", verticalAlign: "middle" }}
        />
      );
    }
  }
  return (
    <InfoOutlinedIcon
      sx={{ color: "text.secondary", verticalAlign: "middle" }}
    />
  );
};

const systemCheckItems = [
  {
    key: "ollama_reachable",
    label: "Ollama Service Reachable",
    icon: <DnsIcon />,
    format: (v) => (v ? "OK" : "Failed"),
  },
  {
    key: "active_model_name",
    label: "Active LLM Name",
    icon: <DnsIcon />,
    format: (v) => v || "N/A",
  },
  {
    key: "active_model_status",
    label: "Active LLM Status",
    icon: <DnsIcon />,
    format: (v) => v || "Unknown",
  },
  {
    key: "active_model_health",
    label: "Ollama Model Loaded",
    icon: <DnsIcon />,
    format: (v) => v || "Unknown",
  },
  {
    key: "llm_basic_response",
    label: "LLM Basic Response Test",
    icon: <DnsIcon />,
    format: (v) => (v ? "OK" : "Failed/Empty"),
  },
  {
    key: "model_count",
    label: "Discovered Ollama Models",
    format: (v) => `${v ?? "N/A"} models found`,
  },
  {
    key: "db_connection",
    label: "Database Connection",
    icon: <StorageIcon />,
    format: (v) => (v ? "OK" : "Failed"),
  },
  {
    key: "document_count_db",
    label: "Document Count (DB)",
    format: (v) => `${v ?? "N/A"} documents in DB`,
  },
  {
    key: "storage_dir_accessible",
    label: "Storage Directory",
    icon: <StorageIcon />,
    format: (v, r) =>
      `${r.storage_dir_path || "N/A"} (${v ? "Accessible" : "Inaccessible"})`,
  },
  {
    key: "upload_dir_accessible",
    label: "Upload Directory",
    icon: <StorageIcon />,
    format: (v, r) =>
      `${r.upload_dir_path || "N/A"} (${v ? "Accessible" : "Inaccessible"})`,
  },
  {
    key: "output_dir_accessible",
    label: "Output Directory",
    icon: <StorageIcon />,
    format: (v, r) =>
      `${r.output_dir_path || "N/A"} (${v ? "Accessible" : "Inaccessible"})`,
  },
  {
    key: "index_storage_exists",
    label: "Index Storage Exists",
    icon: <SpeedIcon />,
    format: (v) => (v ? "OK" : "Not Found/Empty"),
  },
  {
    key: "qa_prompt_loadable",
    label: "QA Default Prompt",
    format: (v) => (v ? "Loadable" : "Not Found/Error"),
  },
  {
    key: "indexing_queue_status",
    label: "Indexing Queue",
    icon: <SpeedIcon />,
    format: (v) => v || "N/A",
  },
  {
    key: "recent_indexing_errors",
    label: "Recent Indexing Errors",
    icon: <ErrorOutlineIcon />,
    format: (v) => v || "N/A",
  },
  {
    key: "backend_log_errors",
    label: "Backend Log Criticals",
    icon: <ErrorOutlineIcon />,
    format: (v) => v || "N/A",
  },
  {
    key: "gpu_tools_available",
    label: "GPU Monitor Available",
    icon: <SpeedIcon />,
    format: (v) => (v ? "Available" : "Unavailable"),
  },
  {
    key: "last_metrics_fetch_status",
    label: "Last Metrics Fetch",
    icon: <SpeedIcon />,
    format: (v) => v || "N/A",
  },
];

const categoryStatusAccent = (statusColor) => {
  if (statusColor === "success") return "success.main";
  if (statusColor === "error") return "error.main";
  if (statusColor === "warning") return "warning.main";
  return "divider";
};

const categorySummaryBg = (theme, statusColor) => {
  if (statusColor === "success") return alpha(theme.palette.success.main, 0.08);
  if (statusColor === "error") return alpha(theme.palette.error.main, 0.1);
  if (statusColor === "warning") return alpha(theme.palette.warning.main, 0.1);
  return theme.palette.action.hover;
};

const renderLegacyDiagnosticsCards = (ds) => (
  <Stack spacing={1} sx={{ mt: 1.5 }}>
    {systemCheckItems.map((item) => {
      const val = ds[item.key];
      const details = val !== undefined ? item.format(val, ds) : "N/A";
      return (
        <Paper
          key={item.key}
          variant="outlined"
          sx={{
            p: 1.25,
            borderRadius: 1,
            bgcolor: "background.paper",
            borderColor: "divider",
          }}
        >
          <Box sx={{ display: "flex", gap: 1.25, alignItems: "flex-start", minWidth: 0 }}>
            <Box sx={{ color: "text.secondary", display: "flex", flexShrink: 0, pt: 0.25 }}>
              {item.icon ? React.cloneElement(item.icon, { fontSize: "small" }) : renderStatusIcon(val, details)}
            </Box>
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Typography variant="body2" fontWeight={600} sx={{ wordBreak: "break-word" }}>
                {item.label}
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5, wordBreak: "break-word", overflowWrap: "anywhere" }}>
                {details}
              </Typography>
            </Box>
          </Box>
        </Paper>
      );
    })}
  </Stack>
);

const DiagnosticsPanel = ({ showMessage }) => {
  const [depth, setDepth] = useState("basic");
  const [isTesting, setIsTesting] = useState(false);
  const [testResults, setTestResults] = useState(null);
  const [testMode, setTestMode] = useState(null); // depth that produced testResults
  const [expandedCategories, setExpandedCategories] = useState({});
  const [isRunningTests, setIsRunningTests] = useState(false);
  const [testSuiteResults, setTestSuiteResults] = useState(null);
  const [testSuiteOutputOpen, setTestSuiteOutputOpen] = useState(false);

  const notify = (message, severity) => {
    if (typeof showMessage === "function") showMessage(message, severity);
  };

  // Enhanced test runner with mode selection
  const handleRunSystemCheck = async (mode = "basic") => {
    // Validate mode parameter
    const validModes = ["basic", "quick", "comprehensive"];
    const validatedMode = validModes.includes(mode) ? mode : "basic";

    setIsTesting(true);
    setTestResults(null);
    setTestMode(validatedMode);
    setExpandedCategories({}); // Reset expanded categories

    const running = DEPTH_OPTIONS.find((o) => o.value === validatedMode)?.running;
    notify(`Running ${running || "system checks"}...`, "info");

    try {
      // Call enhanced API with mode parameter
      const response = await runSelfTest({
        mode: validatedMode,
        include_legacy: true
      });

      if (response?.error && typeof response.error === "string")
        throw new Error(response.error);
      if (response?.results && typeof response.results === "object") {
        setTestResults(response.results);

        // Enhanced success message with status
        const overallStatus = response.results.overall_status || "UNKNOWN";
        const statusDisplay = getStatusDisplay(overallStatus);
        let msg = `System Check Complete - ${statusDisplay.label}`;

        if (response.results.categories) {
          const categoryCount = Object.keys(response.results.categories).length;
          msg += ` (${categoryCount} categories tested)`;
        }

        const severity = overallStatus === "PASS" ? "success" :
          overallStatus === "WARNING" ? "warning" : "error";
        notify(msg, severity);
      } else {
        throw new Error("System check did not return valid results.");
      }
    } catch (error) {
      notify(
        `System Check Error: ${error.message || "Could not run system checks."}`,
        "error",
      );
      setTestResults({
        error: `Failed to run system checks: ${error.message}`,
      });
    } finally {
      setIsTesting(false);
    }
  };

  const handleRunAllTests = async () => {
    setIsRunningTests(true);
    setTestSuiteResults(null);
    setTestSuiteOutputOpen(false);
    notify("Running full test suite...", "info");
    try {
      const response = await runAllTests();
      if (response?.results) {
        setTestSuiteResults(response.results);
        const rc = response.results.returncode;
        const sev = rc === 0 || rc === 4 || rc === 5 ? "success" : "error";
        notify("Test suite finished.", sev);
      } else {
        throw new Error("Invalid response");
      }
    } catch (err) {
      notify(`Test suite error: ${err.message}`, "error");
      setTestSuiteResults({ error: err.message });
    } finally {
      setIsRunningTests(false);
    }
  };

  // Categorized self-test results: card list + muted accordions (no cramped tables)
  const renderCategorizedResults = (results) => {
    if (!results.categories) return null;

    return (
      <Box mt={2} sx={{ width: "100%", minWidth: 0 }}>
        {results.overall_status && (
          <Box
            mb={2}
            sx={{
              display: "flex",
              flexWrap: "wrap",
              gap: 1,
              alignItems: "center",
            }}
          >
            <Chip
              icon={getStatusDisplay(results.overall_status).icon}
              label={`Overall: ${getStatusDisplay(results.overall_status).label}`}
              color={getStatusDisplay(results.overall_status).color}
              variant="outlined"
              size="small"
            />
            {results.execution_time != null && (
              <Chip
                icon={<SpeedIcon sx={{ fontSize: "1rem !important" }} />}
                label={`${Number(results.execution_time).toFixed(2)}s`}
                variant="outlined"
                size="small"
              />
            )}
          </Box>
        )}

        {Object.entries(results.categories).map(([categoryKey, categoryData]) => {
          const isExpanded = expandedCategories[categoryKey] || false;
          const statusDisplay = getStatusDisplay(categoryData.status);

          return (
            <Accordion
              key={categoryKey}
              expanded={isExpanded}
              disableGutters
              elevation={0}
              onChange={() =>
                setExpandedCategories((prev) => ({
                  ...prev,
                  [categoryKey]: !prev[categoryKey],
                }))
              }
              sx={{
                mb: 1,
                border: 1,
                borderColor: "divider",
                borderRadius: 1,
                overflow: "hidden",
                "&:before": { display: "none" },
              }}
            >
              <AccordionSummary
                expandIcon={<ExpandMoreIcon />}
                sx={(theme) => ({
                  minHeight: 48,
                  px: 1.5,
                  borderLeft: "3px solid",
                  borderLeftColor: categoryStatusAccent(statusDisplay.color),
                  bgcolor: categorySummaryBg(theme, statusDisplay.color),
                  "&.Mui-expanded": { minHeight: 48 },
                })}
              >
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    width: "100%",
                    minWidth: 0,
                    flexWrap: "wrap",
                  }}
                >
                  <Box sx={{ color: "text.secondary", display: "flex" }}>{getCategoryIcon(categoryKey)}</Box>
                  <Typography variant="subtitle2" sx={{ flex: "1 1 140px", minWidth: 0, fontWeight: 600 }}>
                    {categoryData.name || categoryKey}
                  </Typography>
                  <Chip
                    icon={statusDisplay.icon}
                    label={statusDisplay.label}
                    color={statusDisplay.color}
                    size="small"
                    variant="outlined"
                  />
                  {categoryData.duration != null && (
                    <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "nowrap" }}>
                      {categoryData.duration.toFixed(2)}s
                    </Typography>
                  )}
                </Box>
              </AccordionSummary>
              <AccordionDetails sx={{ pt: 0, px: 1.5, pb: 1.5, bgcolor: "background.default" }}>
                {categoryData.summary && (
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5, wordBreak: "break-word" }}>
                    {categoryData.summary}
                  </Typography>
                )}

                {categoryData.tests && categoryData.tests.length > 0 && (
                  <Stack spacing={1} sx={{ mt: 0.5 }}>
                    {categoryData.tests.map((test, index) => {
                      const testStatus = getStatusDisplay(test.status);
                      const shortName = (test.name || "").replace(/^.*\//, "");
                      return (
                        <Paper
                          key={index}
                          variant="outlined"
                          sx={{
                            p: 1.25,
                            borderRadius: 1,
                            bgcolor: "background.paper",
                            borderColor: "divider",
                          }}
                        >
                          <Box
                            sx={{
                              display: "flex",
                              flexWrap: "wrap",
                              alignItems: "flex-start",
                              gap: 1,
                              minWidth: 0,
                            }}
                          >
                            <Chip
                              icon={testStatus.icon}
                              label={testStatus.label}
                              color={testStatus.color}
                              size="small"
                              variant="outlined"
                              sx={{ flexShrink: 0 }}
                            />
                            <Box sx={{ flex: "1 1 200px", minWidth: 0 }}>
                              <Typography
                                variant="body2"
                                component="div"
                                sx={{
                                  fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                                  fontSize: "0.8rem",
                                  wordBreak: "break-word",
                                  overflowWrap: "anywhere",
                                }}
                              >
                                {shortName || test.name || "Unnamed test"}
                              </Typography>
                              {test.duration != null && (
                                <Typography variant="caption" color="text.secondary">
                                  {test.duration.toFixed(2)}s
                                </Typography>
                              )}
                            </Box>
                          </Box>
                          {(test.details || test.error_message) && (
                            <Box sx={{ mt: 1, pt: 1, borderTop: 1, borderColor: "divider" }}>
                              {test.details && (
                                <Typography variant="body2" color="text.secondary" sx={{ wordBreak: "break-word" }}>
                                  {test.details}
                                </Typography>
                              )}
                              {test.error_message && (
                                <Typography variant="caption" color="error" component="div" sx={{ mt: 0.5, wordBreak: "break-word" }}>
                                  {test.error_message}
                                </Typography>
                              )}
                            </Box>
                          )}
                        </Paper>
                      );
                    })}
                  </Stack>
                )}
              </AccordionDetails>
            </Accordion>
          );
        })}
      </Box>
    );
  };

  const renderTestSuitePanel = (suite) => {
    if (!suite || typeof suite !== "object") return null;
    const rc = suite.returncode;
    const passed = rc === 0 || rc === 4 || rc === 5;
    const summary = suite.summary || {};
    const counts = summary.counts || {};
    const failures = Array.isArray(summary.failures) ? summary.failures : [];
    const stdout = typeof suite.stdout === "string" ? suite.stdout : "";
    const stderr = typeof suite.stderr === "string" ? suite.stderr : "";

    return (
      <Box
        mt={2}
        sx={{
          width: "100%",
          minWidth: 0,
          p: 1.5,
          borderRadius: 1,
          border: 1,
          borderColor: "divider",
          bgcolor: (theme) => alpha(theme.palette.action.hover, theme.palette.mode === "dark" ? 0.35 : 0.6),
        }}
      >
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1, alignItems: "center" }}>
          <Chip label={passed ? "Suite passed" : "Suite failed"} color={passed ? "success" : "error"} size="small" variant="outlined" />
          <Chip label={`Exit ${rc ?? "?"}`} size="small" variant="outlined" />
          {typeof counts.passed === "number" && (
            <Chip label={`${counts.passed} passed`} size="small" variant="outlined" />
          )}
          {typeof counts.failed === "number" && counts.failed > 0 && (
            <Chip label={`${counts.failed} failed`} size="small" color="error" variant="outlined" />
          )}
          {typeof counts.errors === "number" && counts.errors > 0 && (
            <Chip label={`${counts.errors} errors`} size="small" color="warning" variant="outlined" />
          )}
          {typeof counts.skipped === "number" && counts.skipped > 0 && (
            <Chip label={`${counts.skipped} skipped`} size="small" variant="outlined" />
          )}
        </Box>

        {suite.log_path && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1, wordBreak: "break-all" }}>
            Log: {suite.log_path}
          </Typography>
        )}

        {failures.length > 0 && (
          <Box sx={{ mt: 1.5 }}>
            <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>
              Failure details
            </Typography>
            <Stack spacing={1} sx={{ mt: 1 }}>
              {failures.map((block, i) => (
                <Paper
                  key={i}
                  variant="outlined"
                  sx={{
                    p: 1,
                    borderRadius: 1,
                    bgcolor: "background.paper",
                    borderColor: "error.dark",
                    maxHeight: 220,
                    overflow: "auto",
                  }}
                >
                  <Typography
                    component="pre"
                    variant="caption"
                    sx={{
                      m: 0,
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                      fontSize: "0.7rem",
                    }}
                  >
                    {block}
                  </Typography>
                </Paper>
              ))}
            </Stack>
          </Box>
        )}

        {(stdout || stderr) && (
          <>
            <Divider sx={{ my: 1.5 }} />
            <Button size="small" variant="text" onClick={() => setTestSuiteOutputOpen((o) => !o)} sx={{ textTransform: "none", p: 0, minWidth: 0 }}>
              {testSuiteOutputOpen ? "Hide raw output" : "Show raw output"}
            </Button>
            <Collapse in={testSuiteOutputOpen}>
              {stderr ? (
                <Box sx={{ mt: 1 }}>
                  <Typography variant="caption" color="error" sx={{ fontWeight: 600 }}>
                    stderr
                  </Typography>
                  <Typography
                    component="pre"
                    variant="caption"
                    sx={{
                      display: "block",
                      mt: 0.5,
                      p: 1,
                      borderRadius: 1,
                      bgcolor: "action.hover",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      overflowWrap: "anywhere",
                      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                      fontSize: "0.7rem",
                      maxHeight: 240,
                      overflow: "auto",
                    }}
                  >
                    {stderr}
                  </Typography>
                </Box>
              ) : null}
              {stdout ? (
                <Box sx={{ mt: stderr ? 1.5 : 1 }}>
                  <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
                    stdout
                  </Typography>
                  <Typography
                    component="pre"
                    variant="caption"
                    sx={{
                      display: "block",
                      mt: 0.5,
                      p: 1,
                      borderRadius: 1,
                      bgcolor: "action.hover",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      overflowWrap: "anywhere",
                      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                      fontSize: "0.7rem",
                      maxHeight: 280,
                      overflow: "auto",
                    }}
                  >
                    {stdout}
                  </Typography>
                </Box>
              ) : null}
            </Collapse>
          </>
        )}
      </Box>
    );
  };

  return (
    <Stack spacing={3} sx={{ width: "100%", minWidth: 0 }}>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1, width: "100%", minWidth: 0 }}>
        <Typography variant="subtitle2" color="text.secondary">
          Depth
        </Typography>
        <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", alignItems: "center" }}>
          <ToggleButtonGroup
            exclusive
            size="small"
            value={depth}
            onChange={(_e, value) => {
              if (value) setDepth(value);
            }}
            disabled={isTesting}
            aria-label="Diagnostics depth"
          >
            {DEPTH_OPTIONS.map((option) => (
              <ToggleButton key={option.value} value={option.value}>
                {option.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
          <Button
            variant="contained"
            size="small"
            onClick={() => handleRunSystemCheck(depth)}
            disabled={isTesting}
          >
            {isTesting ? "Running..." : "Run diagnostics"}
          </Button>
          {testMode && !isTesting && (
            <Chip label={`Depth: ${depthLabel(testMode)}`} size="small" variant="outlined" />
          )}
        </Box>
        {testResults && (
          <Box
            sx={{
              mt: 1,
              p: 1.5,
              border: 1,
              borderColor: "divider",
              borderRadius: 1,
              width: "100%",
              minWidth: 0,
              bgcolor: (theme) => alpha(theme.palette.action.hover, theme.palette.mode === "dark" ? 0.25 : 0.5),
            }}
          >
            {testResults.error && typeof testResults.error === "string" ? (
              <MuiAlert severity="error">{testResults.error}</MuiAlert>
            ) : (
              <>
                {testResults.categories && renderCategorizedResults(testResults)}
                {(testResults.legacy_diagnostics || (!testResults.categories && testResults)) && (
                  renderLegacyDiagnosticsCards(testResults.legacy_diagnostics || testResults)
                )}
              </>
            )}
          </Box>
        )}
      </Box>

      <Divider />

      <Box sx={{ display: "flex", flexDirection: "column", gap: 1, width: "100%", minWidth: 0 }}>
        <Typography variant="subtitle2" color="text.secondary">
          Test suite
        </Typography>
        <Button variant="outlined" size="small" onClick={handleRunAllTests} disabled={isRunningTests} sx={{ alignSelf: "flex-start" }}>
          {isRunningTests ? "Running..." : "Run Tests"}
        </Button>
        {testSuiteResults && (
          <Box sx={{ width: "100%", minWidth: 0 }}>
            {testSuiteResults.error ? (
              <MuiAlert severity="error">{testSuiteResults.error}</MuiAlert>
            ) : (
              renderTestSuitePanel(testSuiteResults)
            )}
          </Box>
        )}
      </Box>
    </Stack>
  );
};

export default DiagnosticsPanel;
