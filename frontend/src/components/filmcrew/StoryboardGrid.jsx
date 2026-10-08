import React, { useState } from 'react';
import { formatUiError } from "../../utils/uiError";
import {
  Grid,
  Card,
  CardMedia,
  CardContent,
  Typography,
  Box,
  Button,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  IconButton,
  Tooltip,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import CollapsibleAlert from "../common/CollapsibleAlert";

// Inline SVG so an empty shot never fetches from a third-party CDN.
const NO_STORYBOARD_PLACEHOLDER =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">' +
    '<rect width="320" height="180" fill="#1e1e1e"/>' +
    '<text x="160" y="96" text-anchor="middle" font-family="sans-serif" font-size="16" fill="#888">No storyboard</text>' +
    '</svg>'
  );

// The curator's advice on a frame, as one plain line for the card.
const curatorLine = (advice) => {
  const verdict = advice.verdict === 'flag' ? 'flagged' : 'approved';
  return advice.reason ? `Curator: ${verdict} — ${advice.reason}` : `Curator: ${verdict}`;
};

// Flagged by the curator and not approved by a person: rendering it needs a yes.
const isCuratorFlagged = (shot) =>
  shot.curator_advice?.verdict === 'flag' && !(shot.approved && shot.approved_by === 'person');

const StoryboardGrid = ({ currentStage, shots, onRegenerate, onApproveAll, isApproving }) => {
  const [regenShot, setRegenShot] = useState(null);
  const [promptOverride, setPromptOverride] = useState('');
  const [loading, setLoading] = useState(false);
  const [regenError, setRegenError] = useState(null);
  const [confirmingFlagged, setConfirmingFlagged] = useState(false);

  const flaggedShots = shots.filter(isCuratorFlagged);

  const handleApproveClick = () => {
    if (flaggedShots.length > 0) {
      setConfirmingFlagged(true);
      return;
    }
    onApproveAll();
  };

  const handleRenderAnyway = () => {
    setConfirmingFlagged(false);
    onApproveAll({ confirmFlagged: true });
  };

  const handleRegenClick = (shot) => {
    setRegenShot(shot);
    setPromptOverride('');
    setRegenError(null);
  };

  const handleCloseRegen = () => {
    setRegenShot(null);
    setRegenError(null);
  };

  const handleConfirmRegen = async () => {
    setLoading(true);
    setRegenError(null);
    try {
      await onRegenerate(regenShot.id, { prompt_override: promptOverride });
      setRegenShot(null);
    } catch (err) {
      // Keep dialog open with the error visible — closing silently and not
      // regenerating leaves the user wondering why nothing happened.
      setRegenError(formatUiError(err.response?.data?.error) || 'Regeneration failed. Try again.');
    } finally {
      setLoading(false);
    }
  };

  const canApprove = currentStage === 'awaiting_approval';
  const canRegenerate = currentStage === 'awaiting_approval';

  return (
    <Box sx={{ mt: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h6">Storyboard</Typography>
        {canApprove && (
          <Button
            variant="contained"
            color="success"
            startIcon={<CheckCircleIcon />}
            onClick={handleApproveClick}
            disabled={isApproving}
          >
            {isApproving ? 'Approving…' : 'Approve & Render'}
          </Button>
        )}
      </Box>
      <Grid container spacing={2}>
        {shots.map((shot) => (
          <Grid item xs={12} sm={6} md={4} key={shot.id}>
            <Card sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
              <Box sx={{ position: 'relative' }}>
                <CardMedia
                  component="img"
                  height="180"
                  image={shot.storyboard_image_url || shot.storyboard_image_path || NO_STORYBOARD_PLACEHOLDER}
                  alt={`Shot ${shot.scene_number}.${shot.shot_number}`}
                  sx={{ backgroundColor: '#000' }}
                />
                <Box sx={{ position: 'absolute', top: 8, right: 8, display: 'flex', gap: 1 }}>
                  {shot.approved && (
                    <Chip
                      label={shot.approved_by === 'curator' ? 'Pre-ticked' : 'Approved'}
                      color="success"
                      variant={shot.approved_by === 'curator' ? 'outlined' : 'filled'}
                      size="small"
                      sx={{ height: 24, bgcolor: shot.approved_by === 'curator' ? 'background.paper' : undefined }}
                    />
                  )}
                  <Tooltip title={canRegenerate ? "Regenerate this shot" : "Regeneration is available during storyboard approval"}>
                    <span>
                      <IconButton
                        size="small"
                        aria-label="Regenerate this shot"
                        disabled={!canRegenerate}
                        sx={{ bgcolor: 'rgba(255,255,255,0.8)', '&:hover': { bgcolor: 'white' } }}
                        onClick={() => handleRegenClick(shot)}
                      >
                        <RefreshIcon fontSize="small" />
                      </IconButton>
                    </span>
                  </Tooltip>
                </Box>
              </Box>
              <CardContent sx={{ flexGrow: 1 }}>
                <Typography variant="caption" color="text.secondary" gutterBottom>
                  Scene {shot.scene_number} / Shot {shot.shot_number}
                </Typography>
                <Typography variant="body2" sx={{ 
                  display: '-webkit-box',
                  WebkitLineClamp: 3,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                  mt: 1
                }}>
                  {shot.description}
                </Typography>
                {shot.curator_advice?.verdict && (
                  <Typography
                    variant="caption"
                    display="block"
                    color={shot.curator_advice.verdict === 'flag' ? 'warning.main' : 'success.main'}
                    sx={{ mt: 1 }}
                  >
                    {curatorLine(shot.curator_advice)}
                  </Typography>
                )}
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      <Dialog open={!!regenShot} onClose={handleCloseRegen}>
        <DialogTitle>Regenerate Shot {regenShot?.scene_number}.{regenShot?.shot_number}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 2 }}>
            Optionally override the prompt for this shot. If left blank, the original description will be used.
          </Typography>
          {regenError && (
            <CollapsibleAlert severity="error" sx={{ mb: 2 }} onClose={() => setRegenError(null)}>
              {regenError}
            </CollapsibleAlert>
          )}
          <TextField
            fullWidth
            multiline
            rows={4}
            label="Prompt Override"
            value={promptOverride}
            onChange={(e) => setPromptOverride(e.target.value)}
            placeholder={regenShot?.description}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCloseRegen}>Cancel</Button>
          <Button
            onClick={handleConfirmRegen}
            variant="contained"
            disabled={loading}
          >
            {loading ? 'Rolling...' : 'Regenerate'}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={confirmingFlagged} onClose={() => setConfirmingFlagged(false)}>
        <DialogTitle>Render anyway?</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 1 }}>
            {flaggedShots.length} {flaggedShots.length === 1 ? 'shot was' : 'shots were'} flagged by the curator:
          </Typography>
          {flaggedShots.map((shot) => (
            <Typography key={shot.id} variant="body2" sx={{ ml: 2 }}>
              Scene {shot.scene_number} / Shot {shot.shot_number}
              {shot.curator_advice?.reason ? `: ${shot.curator_advice.reason}` : ''}
            </Typography>
          ))}
          <Typography variant="body2" sx={{ mt: 2 }}>
            Regenerate them first, or render them as they are.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmingFlagged(false)}>Cancel</Button>
          <Button onClick={handleRenderAnyway} variant="contained" color="warning">
            Render anyway
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default StoryboardGrid;
