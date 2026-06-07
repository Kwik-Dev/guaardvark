import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Box,
  Typography,
  Button,
  TextField,
  Paper,
  Stack,
  LinearProgress,
  Chip,
  Alert,
  Divider,
  CircularProgress,
  Collapse,
  MenuItem,
  Link,
  IconButton,
  Tooltip,
} from "@mui/material";
import MusicVideoIcon from "@mui/icons-material/MusicVideo";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import CloseIcon from "@mui/icons-material/Close";

import { uploadFile } from "../api/documentService";
import {
  listMusicVideos,
  getMusicVideo,
  createMusicVideo,
  approveMusicVideo,
  deleteMusicVideo,
  clearMusicVideos,
  documentDownloadUrl,
} from "../api/musicVideoService";

const POLL_MS = 5000;
const TERMINAL = (s) => s === "complete" || (s || "").startsWith("failed");

const stageColor = (stage, status) => {
  if ((status || "").startsWith("failed")) return "error";
  if (stage === "complete") return "success";
  if (stage === "awaiting_approval") return "warning";
  return "info";
};

const MusicVideoPage = () => {
  const [videos, setVideos] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);

  const [name, setName] = useState("");
  const [stylePrompt, setStylePrompt] = useState("");
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const fileInputRef = useRef(null);

  // Advanced render tuning (per video) — collapsed by default; defaults match the
  // backend _settings so leaving this untouched is a no-op.
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [fillMethod, setFillMethod] = useState("forward");
  const [maxStretch, setMaxStretch] = useState("2");
  const [i2vSteps, setI2vSteps] = useState("");
  const [interp, setInterp] = useState("2");

  const refreshList = useCallback(async () => {
    try {
      const data = await listMusicVideos();
      setVideos(data.music_videos || []);
    } catch (e) {
      // non-fatal — keep the last list
    }
  }, []);

  const refreshDetail = useCallback(async (id) => {
    if (!id) return;
    try {
      setDetail(await getMusicVideo(id));
    } catch (e) {
      /* non-fatal */
    }
  }, []);

  // Poll the list, and the selected detail, on an interval.
  useEffect(() => {
    refreshList();
    const t = setInterval(() => {
      refreshList();
      if (selectedId) refreshDetail(selectedId);
    }, POLL_MS);
    return () => clearInterval(t);
  }, [refreshList, refreshDetail, selectedId]);

  useEffect(() => {
    refreshDetail(selectedId);
  }, [selectedId, refreshDetail]);

  const handleCreate = async () => {
    setError(null);
    if (!name.trim() || !stylePrompt.trim() || !file) {
      setError("Name, a song file, and a style prompt are all required.");
      return;
    }
    setBusy(true);
    try {
      // 1) upload the song → Document id. /api/docs/upload resolves the raw body
      // {document_id, filename, job_id, ...}; tolerate a couple of shapes.
      const up = await uploadFile(file, null, "music-video-song", {});
      const songDocId = up?.document_id ?? up?.data?.id ?? up?.id;
      if (!songDocId) throw new Error("Song upload failed (no document id returned).");
      // 2) create the music video (kicks off analysis)
      const settings = {
        fill_method: fillMethod,
        max_stretch: Number(maxStretch) || 2.0,
        interpolation_multiplier: Number(interp) || 1,
      };
      const stepsNum = Number(i2vSteps);
      if (i2vSteps !== "" && stepsNum > 0) settings.i2v_steps = stepsNum;
      const mv = await createMusicVideo({
        name: name.trim(),
        song_document_id: songDocId,
        style_prompt: stylePrompt.trim(),
        settings,
      });
      setName("");
      setStylePrompt("");
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await refreshList();
      setSelectedId(mv.id);
    } catch (e) {
      setError(e?.response?.data?.error || e.message || "Failed to create music video.");
    } finally {
      setBusy(false);
    }
  };

  const handleApprove = async () => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await approveMusicVideo(detail.id);
      setDetail(updated);
      await refreshList();
    } catch (e) {
      setError(e?.response?.data?.error || e.message || "Approve failed.");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id, label) => {
    if (!window.confirm(`Remove "${label}" from the log? The entry is cleared; any rendered output file is left on disk.`)) {
      return;
    }
    setError(null);
    try {
      await deleteMusicVideo(id);
      if (selectedId === id) {
        setSelectedId(null);
        setDetail(null);
      }
      await refreshList();
    } catch (e) {
      setError(e?.response?.data?.error || e.message || "Delete failed.");
    }
  };

  const handleClearFinished = async () => {
    const finished = videos.filter(
      (v) => v.current_stage === "complete" || (v.status || "").startsWith("failed"),
    );
    if (finished.length === 0) return;
    if (!window.confirm(`Clear ${finished.length} finished generation(s) from the log?`)) {
      return;
    }
    setError(null);
    try {
      await clearMusicVideos({ all: false });
      if (selectedId && finished.some((v) => v.id === selectedId)) {
        setSelectedId(null);
        setDetail(null);
      }
      await refreshList();
    } catch (e) {
      setError(e?.response?.data?.error || e.message || "Clear failed.");
    }
  };

  const finishedCount = videos.filter(
    (v) => v.current_stage === "complete" || (v.status || "").startsWith("failed"),
  ).length;

  return (
    <Box sx={{ p: 3, height: "100%", overflow: "auto" }}>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2 }}>
        <MusicVideoIcon fontSize="large" />
        <Box>
          <Typography variant="h5">Music Video</Typography>
          <Typography variant="body2" color="text.secondary">
            Upload a song and a visual style. The beats and energy drive the edits;
            a unique clip is generated per cut and assembled in sync with your song.
          </Typography>
        </Box>
      </Stack>

      <Box sx={{ display: "flex", gap: 3, alignItems: "flex-start", flexWrap: "wrap" }}>
        {/* Create + list */}
        <Stack spacing={2} sx={{ flex: "1 1 360px", minWidth: 320 }}>
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" sx={{ mb: 1.5 }}>New music video</Typography>
            <Stack spacing={1.5}>
              <TextField
                label="Name" size="small" fullWidth value={name}
                onChange={(e) => setName(e.target.value)}
              />
              <TextField
                label="Visual style / prompt" size="small" fullWidth multiline minRows={3}
                placeholder="animation style, deep blue colors, loss and heartache, slow movement"
                value={stylePrompt}
                onChange={(e) => setStylePrompt(e.target.value)}
              />
              <Button
                component="label" variant="outlined" startIcon={<UploadFileIcon />}
                sx={{ justifyContent: "flex-start" }}
              >
                {file ? file.name : "Choose song (mp3 / wav)"}
                <input
                  ref={fileInputRef} type="file" hidden accept="audio/*,.mp3,.wav,.flac"
                  onChange={(e) => setFile(e.target.files?.[0] || null)}
                />
              </Button>

              <Link
                component="button" type="button" variant="body2" underline="hover"
                sx={{ alignSelf: "flex-start" }}
                onClick={() => setShowAdvanced((v) => !v)}
              >
                {showAdvanced ? "▾ Advanced render options" : "▸ Advanced render options"}
              </Link>
              <Collapse in={showAdvanced} unmountOnExit>
                <Stack spacing={1.5} sx={{ pt: 0.5 }}>
                  <TextField
                    select size="small" fullWidth label="Clip motion fill"
                    value={fillMethod} onChange={(e) => setFillMethod(e.target.value)}
                    helperText="How a clip is stretched to fill its cut. Forward = no reverse (fixes the moonwalk)."
                  >
                    <MenuItem value="forward">Forward (no reverse) — recommended</MenuItem>
                    <MenuItem value="boomerang">Boomerang (forward + reverse)</MenuItem>
                    <MenuItem value="loop">Loop (forward repeat)</MenuItem>
                  </TextField>
                  <TextField
                    type="number" size="small" fullWidth label="Clip stretch (×)"
                    value={maxStretch} onChange={(e) => setMaxStretch(e.target.value)}
                    inputProps={{ min: 1, max: 4, step: 0.5 }}
                    helperText="Higher = fewer clips, more slow-mo. 2 = natural. Raise to trade GPU for slowdown."
                  />
                  <TextField
                    select size="small" fullWidth label="Frame interpolation (RIFE)"
                    value={interp} onChange={(e) => setInterp(e.target.value)}
                    helperText="More frames for smoother slow-mo. Cheap (no extra diffusion)."
                  >
                    <MenuItem value="1">Off</MenuItem>
                    <MenuItem value="2">2× (smooth)</MenuItem>
                    <MenuItem value="4">4×</MenuItem>
                  </TextField>
                  <TextField
                    type="number" size="small" fullWidth label="Denoising steps (optional)"
                    value={i2vSteps} onChange={(e) => setI2vSteps(e.target.value)}
                    inputProps={{ min: 8, max: 60, step: 1 }}
                    placeholder="engine default (25)"
                    helperText="Bump a hair for crisper frames when slowing clips down more."
                  />
                </Stack>
              </Collapse>

              {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
              <Button variant="contained" onClick={handleCreate} disabled={busy}>
                {busy ? <CircularProgress size={20} /> : "Create & Analyze"}
              </Button>
            </Stack>
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
              <Typography variant="subtitle1">Your music videos</Typography>
              {finishedCount > 0 && (
                <Link
                  component="button" type="button" variant="caption" underline="hover"
                  color="text.secondary" onClick={handleClearFinished}
                >
                  Clear finished ({finishedCount})
                </Link>
              )}
            </Stack>
            {videos.length === 0 && (
              <Typography variant="body2" color="text.secondary">None yet.</Typography>
            )}
            <Stack spacing={1}>
              {videos.map((v) => (
                <Paper
                  key={v.id}
                  variant="outlined"
                  onClick={() => setSelectedId(v.id)}
                  sx={{
                    p: 1.25, cursor: "pointer",
                    borderColor: v.id === selectedId ? "primary.main" : "divider",
                  }}
                >
                  <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
                    <Typography variant="body2" noWrap sx={{ flex: 1, minWidth: 0 }}>{v.name}</Typography>
                    <Chip
                      size="small" label={v.current_stage}
                      color={stageColor(v.current_stage, v.status)}
                    />
                    <Tooltip title="Remove from log">
                      <IconButton
                        size="small"
                        onClick={(e) => { e.stopPropagation(); handleDelete(v.id, v.name); }}
                        sx={{ opacity: 0.5, "&:hover": { opacity: 1 } }}
                      >
                        <CloseIcon fontSize="inherit" />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                </Paper>
              ))}
            </Stack>
          </Paper>
        </Stack>

        {/* Detail */}
        <Box sx={{ flex: "2 1 460px", minWidth: 360 }}>
          {!detail ? (
            <Paper variant="outlined" sx={{ p: 3 }}>
              <Typography variant="body2" color="text.secondary">
                Select a music video to see its progress.
              </Typography>
            </Paper>
          ) : (
            <Paper variant="outlined" sx={{ p: 2.5 }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
                <Typography variant="h6">{detail.name}</Typography>
                <Chip label={detail.current_stage} color={stageColor(detail.current_stage, detail.status)} />
              </Stack>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                {detail.style_prompt}
              </Typography>
              <Divider sx={{ mb: 2 }} />

              {detail.current_stage === "analyzing" && (
                <Stack spacing={1}>
                  <Typography variant="body2">Analyzing the song for beats &amp; energy…</Typography>
                  <LinearProgress />
                </Stack>
              )}

              {detail.current_stage === "awaiting_approval" && detail.estimate && (
                <Stack spacing={1.5}>
                  <Alert severity="warning">
                    Ready to generate <b>{detail.estimate.clips_to_generate}</b> unique clips
                    (one per cut). Estimated GPU time: <b>{detail.estimate.estimated_human}</b>.
                    Nothing has used the GPU yet — approve to begin.
                  </Alert>
                  <Box>
                    <Button variant="contained" color="warning" onClick={handleApprove} disabled={busy}>
                      {busy ? <CircularProgress size={20} /> : "Approve & Generate"}
                    </Button>
                  </Box>
                </Stack>
              )}

              {detail.current_stage === "generating" && (
                <Stack spacing={1}>
                  <Typography variant="body2">
                    Generating clips: {detail.clips_done} / {detail.clip_count}
                  </Typography>
                  <LinearProgress
                    variant={detail.clip_count ? "determinate" : "indeterminate"}
                    value={detail.clip_count ? (detail.clips_done / detail.clip_count) * 100 : 0}
                  />
                </Stack>
              )}

              {detail.current_stage === "assembling" && (
                <Stack spacing={1}>
                  <Typography variant="body2">Assembling the final cut in sync with your song…</Typography>
                  <LinearProgress />
                </Stack>
              )}

              {detail.current_stage === "complete" && detail.output_document_id && (
                <Stack spacing={1}>
                  <Typography variant="subtitle2">Done — your music video:</Typography>
                  <video
                    controls
                    style={{ width: "100%", borderRadius: 8, background: "#000" }}
                    src={documentDownloadUrl(detail.output_document_id)}
                  />
                </Stack>
              )}

              {(detail.status || "").startsWith("failed") && (
                <Alert severity="error">
                  Failed at stage <b>{detail.error_blob?.stage || detail.status}</b>:{" "}
                  {String(detail.error_blob?.error || "unknown error")}
                </Alert>
              )}

              <Divider sx={{ my: 2 }} />
              <Typography variant="caption" color="text.secondary">
                {detail.cut_count} cuts · {detail.clips_done}/{detail.clip_count} clips rendered
              </Typography>
            </Paper>
          )}
        </Box>
      </Box>
    </Box>
  );
};

export default MusicVideoPage;
