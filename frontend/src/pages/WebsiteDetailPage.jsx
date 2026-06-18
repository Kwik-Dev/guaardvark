// frontend/src/pages/WebsiteDetailPage.jsx
// Per-website detail page (replaces the settings-only modal as the primary click target).
// Phase 1 tabs: Overview + Indexing. Settings live behind the ⚙ button (the existing
// WebsiteActionModal, now demoted to settings-only). Reads existing endpoints — no backend change.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Alert as MuiAlert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Grid,
  Paper,
  Snackbar,
  Stack,
  Tab,
  Tabs,
  Typography,
} from "@mui/material";
import SettingsIcon from "@mui/icons-material/Settings";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import TravelExploreIcon from "@mui/icons-material/TravelExplore";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import EventIcon from "@mui/icons-material/Event";

import { getWebsite, updateWebsite, deleteWebsite, scrapeWebsite } from "../api/websiteService";
import * as wordpressService from "../api/wordpressService";
import WebsiteActionModal from "../components/modals/WebsiteActionModal";
import IndexingPanel from "../components/website/IndexingPanel";
import CrawledPagesPanel from "../components/website/CrawledPagesPanel";
import PageLayout from "../components/layout/PageLayout";
import { useStatus } from "../contexts/StatusContext";

const AlertSnackbar = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

const formatDate = (iso) => {
  if (!iso) return "Never";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};

const StatusChip = ({ status }) => {
  if (!status) return null;
  const normalized = status.toLowerCase();
  let color = "default";
  if (["active", "online", "indexed"].includes(normalized)) color = "success";
  else if (["error", "offline"].includes(normalized)) color = "error";
  else if (["crawling", "indexing"].includes(normalized)) color = "warning";
  return (
    <Chip
      label={status}
      color={color}
      size="small"
      sx={{ textTransform: "capitalize" }}
    />
  );
};

const Field = ({ label, children }) => (
  <Box sx={{ mb: 1.5 }}>
    <Typography variant="caption" color="text.secondary" display="block">
      {label}
    </Typography>
    <Typography variant="body2" sx={{ wordBreak: "break-all" }}>
      {children}
    </Typography>
  </Box>
);

const WebsiteDetailPage = () => {
  const { websiteId } = useParams();
  const navigate = useNavigate();
  const { activeModel } = useStatus();

  const [website, setWebsite] = useState(null);
  const [wpSite, setWpSite] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tabValue, setTabValue] = useState(0);
  const [crawling, setCrawling] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [feedback, setFeedback] = useState({ open: false, message: "", severity: "info" });

  const loadWebsite = useCallback(async () => {
    if (!websiteId) return;
    setLoading(true);
    setError(null);
    try {
      const [site, wpResp] = await Promise.all([
        getWebsite(websiteId),
        wordpressService.getWordPressSites().catch(() => null),
      ]);
      if (site && site.error) throw new Error(site.error);
      setWebsite(site);
      const wpList = wpResp?.success && Array.isArray(wpResp.data) ? wpResp.data : [];
      setWpSite(wpList.find((wp) => String(wp.website_id) === String(websiteId)) || null);
    } catch (err) {
      setError(err.message || "Failed to load website.");
    } finally {
      setLoading(false);
    }
  }, [websiteId]);

  useEffect(() => {
    loadWebsite();
  }, [loadWebsite]);

  const handleCloseFeedback = (event, reason) => {
    if (reason === "clickaway") return;
    setFeedback((prev) => ({ ...prev, open: false }));
  };

  const handleCrawl = async () => {
    setCrawling(true);
    setFeedback({ open: true, message: "Queuing crawl…", severity: "info" });
    try {
      const res = await scrapeWebsite(website.id);
      setFeedback({
        open: true,
        message: res?.message || "Crawl queued — watch Activity for progress.",
        severity: "success",
      });
    } catch (err) {
      setFeedback({
        open: true,
        message: `Could not queue crawl: ${err.message || "Unknown error"}`,
        severity: "error",
      });
    } finally {
      setCrawling(false);
    }
  };

  // WebsiteActionModal calls onSave(id, data) for an existing website.
  const handleSaveSettings = async (id, data) => {
    setIsSaving(true);
    try {
      const resp = await updateWebsite(id, data);
      if (resp && resp.error) throw new Error(resp.error.message || resp.error);
      setFeedback({ open: true, message: "Website updated successfully!", severity: "success" });
      setSettingsOpen(false);
      loadWebsite();
    } catch (err) {
      setFeedback({
        open: true,
        message: err.message || "Error updating website.",
        severity: "error",
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async (id, url) => {
    if (
      !window.confirm(
        `Are you sure you want to delete website "${url || "N/A"}" (ID: ${id})? This action cannot be undone.`,
      )
    )
      return;
    setIsSaving(true);
    try {
      await deleteWebsite(id);
      navigate("/websites");
    } catch (err) {
      setFeedback({
        open: true,
        message: err.message || "Failed to delete website.",
        severity: "error",
      });
      setIsSaving(false);
    }
  };

  const headerContent = useMemo(
    () =>
      website ? (
        <Box sx={{ px: 2, pb: 0.5 }}>
          <Typography variant="caption" color="text.secondary">
            ID: {website.id} | Project: {website.project?.name || "N/A"} | Client:{" "}
            {website.client?.name || "N/A"} | Last crawled: {formatDate(website.last_crawled)}
          </Typography>
        </Box>
      ) : null,
    [website],
  );

  return (
    <PageLayout
      title={website ? `Website: ${website.url}` : "Website"}
      variant="standard"
      actions={
        <Stack direction="row" spacing={1}>
          <Button
            variant="outlined"
            size="small"
            color="inherit"
            startIcon={<ArrowBackIcon />}
            onClick={() => navigate("/websites")}
          >
            Back
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<SettingsIcon />}
            onClick={() => setSettingsOpen(true)}
            disabled={!website}
          >
            Settings
          </Button>
        </Stack>
      }
      modelStatus
      activeModel={activeModel}
      headerContent={headerContent}
    >
      <Snackbar
        open={feedback.open}
        autoHideDuration={6000}
        onClose={handleCloseFeedback}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <AlertSnackbar
          onClose={handleCloseFeedback}
          severity={feedback.severity || "info"}
          sx={{ width: "100%" }}
        >
          {feedback.message}
        </AlertSnackbar>
      </Snackbar>

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress />
        </Box>
      )}

      {!loading && error && (
        <MuiAlert severity="error" sx={{ m: 2 }}>
          {error}
        </MuiAlert>
      )}

      {!loading && !error && website && (
        <>
          <Box sx={{ borderBottom: 1, borderColor: "divider", mx: 1.5 }}>
            <Tabs
              value={tabValue}
              onChange={(e, v) => setTabValue(v)}
              aria-label="website detail tabs"
              sx={{
                minHeight: "40px",
                "& .MuiTab-root": { minHeight: "40px", fontSize: "0.875rem", py: 1 },
              }}
            >
              <Tab label="Overview" id="website-tab-overview" />
              <Tab label="Pages" id="website-tab-pages" />
              <Tab label="Indexing" id="website-tab-indexing" />
            </Tabs>
          </Box>

          <Box sx={{ flexGrow: 1, overflowY: "auto", p: 1.5, pt: 2 }}>
            {tabValue === 0 && (
              <Grid container spacing={2}>
                <Grid item xs={12} md={6}>
                  <Paper variant="outlined" sx={{ p: 2 }}>
                    <Typography variant="subtitle2" gutterBottom>
                      Details
                    </Typography>
                    <Field label="URL">
                      <a href={website.url} target="_blank" rel="noreferrer">
                        {website.url}
                      </a>
                    </Field>
                    <Field label="Status">
                      <StatusChip status={website.status} />
                    </Field>
                    <Field label="Sitemap">{website.sitemap || "—"}</Field>
                    <Field label="Competitor URL">{website.competitor_url || "—"}</Field>
                    <Field label="Linked documents">
                      {website.document_count ?? "N/A"}
                    </Field>
                    <Field label="Last crawled">{formatDate(website.last_crawled)}</Field>
                  </Paper>
                </Grid>

                <Grid item xs={12} md={6}>
                  <Paper variant="outlined" sx={{ p: 2 }}>
                    <Typography variant="subtitle2" gutterBottom>
                      Actions
                    </Typography>
                    <Stack spacing={1.5} sx={{ mt: 1 }}>
                      <Button
                        variant="outlined"
                        startIcon={<TravelExploreIcon />}
                        onClick={handleCrawl}
                        disabled={crawling}
                      >
                        {crawling ? "Queuing…" : "Crawl now"}
                      </Button>
                      <Button
                        variant="outlined"
                        startIcon={<EventIcon />}
                        onClick={() => navigate(`/tasks?website_id=${website.id}`)}
                      >
                        Schedule task
                      </Button>
                      {wpSite && (
                        <Button
                          variant="outlined"
                          startIcon={<AutoAwesomeIcon />}
                          onClick={() => navigate(`/wordpress/pages?site_id=${wpSite.id}`)}
                        >
                          Manage WordPress pages
                        </Button>
                      )}
                    </Stack>
                    {wpSite && (
                      <Box sx={{ mt: 2 }}>
                        <Chip
                          label="WordPress Connected"
                          color="success"
                          size="small"
                          icon={<AutoAwesomeIcon />}
                        />
                      </Box>
                    )}
                  </Paper>
                </Grid>
              </Grid>
            )}

            {tabValue === 1 && <CrawledPagesPanel website={website} onFeedback={setFeedback} />}

            {tabValue === 2 && <IndexingPanel website={website} onFeedback={setFeedback} />}
          </Box>
        </>
      )}

      {settingsOpen && website && (
        <WebsiteActionModal
          open={settingsOpen}
          onClose={() => {
            if (!isSaving) setSettingsOpen(false);
          }}
          websiteData={website}
          onSave={handleSaveSettings}
          onDelete={handleDelete}
          isSaving={isSaving}
        />
      )}
    </PageLayout>
  );
};

export default WebsiteDetailPage;
