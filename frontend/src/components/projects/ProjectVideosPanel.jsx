import React, { useCallback, useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  CardMedia,
  Chip,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import MusicVideoIcon from "@mui/icons-material/MusicVideo";
import MovieIcon from "@mui/icons-material/Movie";
import { listProjectVideos, updateMusicVideo } from "../../api/musicVideoService";
import { formatUiError } from "../../utils/uiError";

const POLL_MS = 10000;

/** Where a video project opens. */
export function videoHref(row) {
  if (row.kind === "music_video") return `/music-video?id=${row.id}&project_id=${row.project_id}`;
  return "/film-crew";
}

const kindLabel = (kind) => (kind === "music_video" ? "Music video" : kind === "film" ? "Film Crew" : kind);

/**
 * The Videos tab of a Project: every music video / Film Crew production filed
 * under it, with its stage and progress, and buttons to start a new one here.
 */
export default function ProjectVideosPanel({ projectId }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const data = await listProjectVideos(projectId);
      setRows(data.videos || []);
      setError(null);
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message || "Could not load videos.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const unfile = async (row) => {
    if (!window.confirm(`Remove “${row.name}” from this project? The video itself is kept.`)) return;
    try {
      await updateMusicVideo(row.id, { project_id: null });
      await load();
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message || "Could not remove it from the project.");
    }
  };

  return (
    <Box>
      <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
        <Button
          variant="contained" size="small" startIcon={<MusicVideoIcon />}
          component={RouterLink} to={`/music-video?project_id=${projectId}`}
        >
          New music video
        </Button>
        <Button
          variant="outlined" size="small" startIcon={<MovieIcon />}
          component={RouterLink} to="/film-crew"
        >
          Film Crew
        </Button>
      </Stack>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {loading ? (
        <CircularProgress size={24} />
      ) : rows.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No videos in this project yet. Start a music video here, or file an existing one under
          this project from the Music Video page.
        </Typography>
      ) : (
        <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}>
          {rows.map((row) => (
            <Card key={`${row.kind}-${row.id}`} variant="outlined">
              <CardActionArea component={RouterLink} to={videoHref(row)}>
                {row.thumbnail_url ? (
                  <CardMedia component="img" height="110" image={row.thumbnail_url} alt="" />
                ) : (
                  <Box sx={{ height: 110, display: "flex", alignItems: "center", justifyContent: "center", bgcolor: "action.hover" }}>
                    {row.kind === "music_video" ? <MusicVideoIcon /> : <MovieIcon />}
                  </Box>
                )}
                <CardContent sx={{ pb: 1 }}>
                  <Typography variant="subtitle2" noWrap>{row.name}</Typography>
                  <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }} flexWrap="wrap" useFlexGap>
                    <Chip size="small" variant="outlined" label={kindLabel(row.kind)} />
                    <Chip size="small" label={row.current_stage} />
                    {row.overall_pct != null && row.current_stage !== "complete" && (
                      <Chip size="small" color="primary" variant="outlined" label={`${Math.round(row.overall_pct)}%`} />
                    )}
                  </Stack>
                </CardContent>
              </CardActionArea>
              {row.kind === "music_video" && (
                <Box sx={{ px: 1, pb: 1 }}>
                  <Button size="small" color="inherit" onClick={() => unfile(row)}>
                    Remove from project
                  </Button>
                </Box>
              )}
            </Card>
          ))}
        </Box>
      )}
    </Box>
  );
}
