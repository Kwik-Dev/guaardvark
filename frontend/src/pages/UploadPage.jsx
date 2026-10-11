// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from the project maintainer.
import React, { useState, useCallback, useEffect, useRef } from "react";
import {
  Box,
  Button,
  Typography,
  Paper,
  CircularProgress,
  Alert,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  // Progress moved to ProgressFooterBar
  Chip,
} from "@mui/material";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import DescriptionIcon from "@mui/icons-material/Description";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorIcon from "@mui/icons-material/Error";
import HourglassTopIcon from "@mui/icons-material/HourglassTop"; // Icon for indexing

import { uploadFile, getDocuments } from "../api";
import { useStatus } from "../contexts/StatusContext";
import PageLayout from "../components/layout/PageLayout";
import { formatTimestamp } from "../utils/fileTypeUtils";
import useFileDropZone from "../hooks/useFileDropZone";

// The picker's filter only. /api/docs/upload keeps no extension list of its
// own (the loaders decide what gets indexed), so neither the picker nor the
// drop zone refuses a file here; the server's answer is shown as it comes.
const ACCEPTED_EXTENSIONS =
  ".txt,.md,.pdf,.docx,.html,.htm,.json,.csv,.xml,.py,.js,.jsx,.java,.c,.cpp,.h,.cs,.go,.php,.rb,.swift,.kt,.rs,.scala,.zip";

// Recent Uploads refreshes itself while a row is still being indexed, so a
// new upload turns INDEXED without a reload. The cap counts every tick,
// answered or not, so a paused queue or a backend that is down stops the
// polling after two minutes instead of never.
const RECENT_REFRESH_MS = 3000;
const RECENT_REFRESH_MAX_POLLS = 40;
const statusOf = (doc) => String(doc?.index_status || "").toUpperCase();
const isInFlight = (doc) => ["INDEXING", "PENDING"].includes(statusOf(doc));
// The row this page just uploaded is followed until it shows one of these.
// STORED is not one: for a document type the backend's resume tick queues a
// STORED row like a PENDING one (text uploads sit at STORED until the worker
// takes them), so it means "stored, waiting for the indexer".
const WATCHED_SETTLED = new Set(["INDEXED", "ERROR", "STORAGE_FAILED", "STORED_TRUNCATED"]);

const IndexStatusChip = ({ status }) => {
  let color = "default";
  let label = status || "Unknown";
  switch (status?.toUpperCase()) {
    case "INDEXED":
      color = "success";
      break;
    case "ERROR":
      color = "error";
      break;
    case "PENDING":
      color = "warning";
      break;
    case "INDEXING":
      color = "info";
      break;
    case "UPLOADED":
      color = "default";
      break;
    default:
      label = status ? String(status) : "Unknown";
      break;
  }
  return (
    <Chip
      label={label}
      color={color}
      size="small"
      sx={{ textTransform: "capitalize" }}
    />
  );
};

const UploadPage = () => {
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadStatus, setUploadStatus] = useState("idle"); // idle, uploading, indexing, success, error
  const [errorMessage, setErrorMessage] = useState("");
  const [successMessage, setSuccessMessage] = useState("");
  const [_uploadProgress, setUploadProgress] = useState(0); // Progress state if using XHR
  const [recentDocs, setRecentDocs] = useState([]);
  const [isLoadingRecent, setIsLoadingRecent] = useState(true);
  // {id, name} of the file just uploaded here, until its row settles.
  const [watched, setWatched] = useState(null);
  const refreshCountRef = useRef(0);
  const { activeModel, isLoadingModel, modelError } = useStatus();
  const fileInputRef = useRef(null);

  // silent: refresh the rows in place without the loading spinner.
  const fetchRecentDocs = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setIsLoadingRecent(true);
    try {
      const result = await getDocuments({ page: 1, perPage: 5 });
      if (result && result.error) throw new Error(result.error);
      setRecentDocs(result?.documents || result?.items || []);
    } catch (err) {
      console.error("Failed to fetch recent documents:", err);
      if (!silent) setRecentDocs([]);
    } finally {
      if (!silent) setIsLoadingRecent(false);
    }
  }, []);

  useEffect(() => {
    fetchRecentDocs();
  }, [fetchRecentDocs]);

  // The row just uploaded here: once it is in the list with a settled
  // status, the upload's own message says how it ended.
  useEffect(() => {
    if (!watched) return;
    const row = recentDocs.find((doc) => doc.id === watched.id);
    if (!row || !WATCHED_SETTLED.has(statusOf(row))) return;
    setWatched(null);
    if (statusOf(row) === "INDEXED") {
      setSuccessMessage(`File '${watched.name}' uploaded and indexed.`);
      setUploadStatus("success");
    } else {
      setErrorMessage(
        `File '${watched.name}' uploaded, but indexing failed${row.error_message ? `: ${row.error_message}` : "."}`,
      );
      setUploadStatus("error");
    }
  }, [recentDocs, watched]);

  // Keep refreshing while any recent row is still indexing, or while the
  // row just uploaded has not yet appeared with a settled status; stop when
  // neither holds or the cap trips.
  useEffect(() => {
    const busy = watched != null || recentDocs.some(isInFlight);
    if (!busy) {
      refreshCountRef.current = 0;
      return undefined;
    }
    const interval = setInterval(() => {
      refreshCountRef.current += 1;
      if (refreshCountRef.current > RECENT_REFRESH_MAX_POLLS) {
        clearInterval(interval);
        refreshCountRef.current = 0;
        if (watched) {
          setWatched(null);
          setSuccessMessage(
            `File '${watched.name}' uploaded; indexing is still running. Recent Uploads shows the result on a reload.`,
          );
          setUploadStatus("success");
        }
        return;
      }
      fetchRecentDocs({ silent: true });
    }, RECENT_REFRESH_MS);
    return () => clearInterval(interval);
  }, [recentDocs, watched, fetchRecentDocs]);

  // One path for the picker and the drop zone.
  const selectFile = useCallback((file) => {
    if (!file) return;
    setSelectedFile(file);
    setUploadStatus("idle"); // Reset status when new file selected
    setErrorMessage("");
    setSuccessMessage("");
    setUploadProgress(0);
  }, []);

  const handleFileChange = (event) => {
    const file = event.target.files && event.target.files[0];
    // Clear the input so picking the same file again still fires onChange.
    event.target.value = "";
    selectFile(file);
  };

  const openPicker = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const drop = useFileDropZone({ onFiles: (files) => selectFile(files[0]) });

  const handleUpload = useCallback(async () => {
    if (!selectedFile) {
      setErrorMessage("Please select a file first.");
      return;
    }

    setUploadStatus("uploading");
    setErrorMessage("");
    setSuccessMessage("");
    setUploadProgress(0); // Reset progress
    
    // Progress events now handled by backend SocketIO system

    try {
      // --- Call uploadFile API ---
      const uploadResult = await uploadFile(
        selectedFile,
        null, // projectId 
        null, // tags
        {},   // metadata
        null, // signal
        null  // progress callback - handled elsewhere
      );

      if (uploadResult && uploadResult.error) {
        // Handle errors returned from uploadFile function itself
        throw new Error(uploadResult.error);
      }

      if (uploadResult && uploadResult.document_id) {
        // /api/docs/upload queues the indexing task itself (auto_index), so
        // the page does not call /api/index/<id> on top of it: that queued the
        // same document a second time under a second footer job. Recent
        // Uploads below follows the row until it is INDEXED.
        if (uploadResult.indexing_queued === false) {
          setErrorMessage(
            `File '${selectedFile.name}' uploaded (ID: ${uploadResult.document_id}), but indexing could not be queued${uploadResult.indexing_error ? `: ${uploadResult.indexing_error}` : "."}`,
          );
          setUploadStatus("error");
          fetchRecentDocs();
          return;
        }
        setSuccessMessage(
          `File '${selectedFile.name}' uploaded (ID: ${uploadResult.document_id}); indexing queued.`,
        );
        setUploadStatus("indexing");
        refreshCountRef.current = 0;
        setWatched({ id: uploadResult.document_id, name: selectedFile.name });
        fetchRecentDocs();
      } else {
        // Handle unexpected success response format from upload
        console.warn(
          "Upload succeeded but response format unexpected:",
          uploadResult,
        );
        throw new Error(
          "Upload succeeded but received an unexpected response.",
        );
      }
    } catch (error) {
      console.error("Upload failed:", error);
      setErrorMessage(
        error.message || "An unknown error occurred during upload.",
      );
      setUploadStatus("error");
      
      // Error events now handled by backend SocketIO system
    } finally {
      // Reset selected file after attempt? Optional.
      // setSelectedFile(null);
      // event.target.value = null; // Need access to the input event target if doing this
      setUploadProgress(0); // Reset progress indicator
    }
  }, [selectedFile, fetchRecentDocs]);

  // Note: Basic fetch doesn't support upload progress easily.
  // For progress, you'd typically use XMLHttpRequest directly.
  // This example doesn't implement visual progress tracking during upload.

  return (
    <PageLayout
      title="Import Documents"
      variant="standard"
      modelStatus
      activeModel={isLoadingModel ? "Loading..." : modelError ? "Error" : activeModel || "Default"}
    >
      <Paper elevation={2} sx={{ p: 3 }}>
        <Box
          {...drop.dropProps}
          onClick={openPicker}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              openPicker();
            }
          }}
          role="button"
          tabIndex={0}
          aria-label="Select a file to import"
          data-testid="upload-drop-zone"
          sx={{
            border: "2px dashed",
            borderColor: drop.isDragActive ? "primary.main" : "divider",
            borderRadius: 1,
            p: 3,
            textAlign: "center",
            mb: 2,
            cursor: "pointer",
          }}
        >
          <input
            ref={fileInputRef}
            accept={ACCEPTED_EXTENSIONS}
            style={{ display: "none" }}
            id="raised-button-file"
            type="file"
            onChange={handleFileChange}
          />
          <Button
            variant="outlined"
            startIcon={<UploadFileIcon />}
            onClick={(event) => {
              event.stopPropagation();
              openPicker();
            }}
          >
            Select File
          </Button>
          {selectedFile && (
            <Typography variant="body1" sx={{ mt: 2 }}>
              Selected: {selectedFile.name} (
              {(selectedFile.size / 1024).toFixed(2)} KB)
            </Typography>
          )}
          {!selectedFile && (
            <Typography variant="body2" sx={{ color: "text.secondary", mt: 1 }}>
              Drag and drop or click to select a file.
            </Typography>
          )}
        </Box>

        <Button
          variant="contained"
          onClick={handleUpload}
          disabled={
            !selectedFile ||
            uploadStatus === "uploading" ||
            uploadStatus === "indexing"
          }
          fullWidth
          startIcon={
            uploadStatus === "uploading" || uploadStatus === "indexing" ? (
              <CircularProgress size={20} color="inherit" />
            ) : null
          }
        >
          {uploadStatus === "uploading"
            ? "Importing..."
            : uploadStatus === "indexing"
              ? "Indexing..."
              : "Import and Index"}
        </Button>

        {/* Status Messages */}
        {uploadStatus === "success" && successMessage && (
          <Alert
            severity="success"
            sx={{ mt: 2 }}
            icon={<CheckCircleIcon fontSize="inherit" />}
          >
            {successMessage}
          </Alert>
        )}
        {uploadStatus === "indexing" &&
          successMessage && ( // Shown while the uploaded row is being indexed
            <Alert
              severity="info"
              sx={{ mt: 2 }}
              icon={<HourglassTopIcon fontSize="inherit" />}
            >
              {successMessage}
            </Alert>
          )}
        {uploadStatus === "error" && errorMessage && (
          <Alert
            severity="error"
            sx={{ mt: 2 }}
            icon={<ErrorIcon fontSize="inherit" />}
          >
            {errorMessage}
          </Alert>
        )}

        <Typography variant="h6" sx={{ mt: 4 }}>
          Recent Uploads
        </Typography>
        {isLoadingRecent ? (
          <Box sx={{ display: "flex", justifyContent: "center", my: 2 }}>
            <CircularProgress size={20} />
          </Box>
        ) : recentDocs.length === 0 ? (
          <Typography variant="body2" sx={{ my: 2, fontStyle: "italic" }}>
            No recent uploads.
          </Typography>
        ) : (
          <List dense>
            {recentDocs.map((doc) => (
              <ListItem key={doc.id} divider>
                <ListItemIcon sx={{ minWidth: "36px" }}>
                  <DescriptionIcon
                    color={
                      doc.index_status === "INDEXED"
                        ? "success"
                        : doc.index_status === "ERROR"
                          ? "error"
                          : "action"
                    }
                    fontSize="small"
                  />
                </ListItemIcon>
                <ListItemText
                  primaryTypographyProps={{ variant: "body2" }}
                  secondaryTypographyProps={{ variant: "caption" }}
                  primary={doc.filename || `Document ${doc.id}`}
                  secondary={
                    <>
                      <IndexStatusChip status={doc.index_status} /> Imported:{" "}
                      {formatTimestamp(doc.uploaded_at)}
                    </>
                  }
                />
              </ListItem>
            ))}
          </List>
        )}
      </Paper>
    </PageLayout>
  );
};

export default UploadPage;
