import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box, Tabs, Tab, Typography, Button, TextField, Card, CardMedia, CardContent,
  CardActions, Chip, CircularProgress, Alert, Dialog, DialogTitle, DialogContent,
  DialogActions, Grid, IconButton, Tooltip, Divider, Link,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import RefreshIcon from '@mui/icons-material/Refresh';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked';
import {
  getCastSubject, updateCastSubject, planCharacter, generateSamples,
  listSamples, regenerateSample, approveSamples, trainSubject,
} from '../api/productionService';
import { SubjectThumb } from '../components/filmcrew/CastLibraryView';
import DragDropImageUpload from '../components/filmcrew/DragDropImageUpload';

const POLL_MS = 5000;
const POLL_CAP = 180; // 15 min safety cap on a generate/train poll loop

// A sample is "in flight" while its image is still being produced.
const isPending = (s) => s.status === 'pending' || s.status === 'generating';

const StatusChip = ({ status }) => {
  const color = status === 'done' ? 'success'
    : status === 'failed' ? 'error'
    : status === 'generating' ? 'warning' : 'default';
  return <Chip size="small" label={status} color={color} />;
};

const CastMemberPage = () => {
  const { subjectId } = useParams();
  const navigate = useNavigate();

  const [subject, setSubject] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState(0);

  // Overview edit form.
  const [form, setForm] = useState({ name: '', description: '', trigger_word: '', voice_id: '' });
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState(false);

  // Generate-character state.
  const [samples, setSamples] = useState([]);
  const [planning, setPlanning] = useState(false);
  const [busy, setBusy] = useState(false);        // generate/train dispatch in flight
  const [polling, setPolling] = useState(false);
  const pollCount = useRef(0);
  const [regenTarget, setRegenTarget] = useState(null); // sample being regenerated
  const [regenPrompt, setRegenPrompt] = useState('');

  const loadSubject = useCallback(async () => {
    const s = await getCastSubject(subjectId);
    setSubject(s);
    if (s) setForm({
      name: s.name || '', description: s.description || '',
      trigger_word: s.trigger_word || '', voice_id: s.voice_id || '',
    });
    return s;
  }, [subjectId]);

  const loadSamples = useCallback(async () => {
    const data = await listSamples(subjectId);
    setSamples(data.samples || []);
    return data.samples || [];
  }, [subjectId]);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const s = await loadSubject();
        if (alive && !s) setError('Subject not found.');
        await loadSamples();
      } catch (e) {
        if (alive) setError('Failed to load this cast member.');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [loadSubject, loadSamples]);

  // Poll while a generate/regenerate/train job is running; auto-stop when the
  // work settles (no pending samples and not training) or the cap is hit.
  useEffect(() => {
    if (!polling) return undefined;
    const id = setInterval(async () => {
      pollCount.current += 1;
      try {
        const [s, rows] = await Promise.all([loadSubject(), loadSamples()]);
        const stillGenerating = (rows || []).some(isPending);
        const stillTraining = s?.training_status === 'training';
        if ((!stillGenerating && !stillTraining) || pollCount.current >= POLL_CAP) {
          setPolling(false);
        }
      } catch (e) {
        // transient — keep polling until the cap
      }
    }, POLL_MS);
    return () => clearInterval(id);
  }, [polling, loadSubject, loadSamples]);

  const startPolling = () => { pollCount.current = 0; setPolling(true); };

  const handleSave = async () => {
    setSaving(true); setError(null);
    try {
      await updateCastSubject(subjectId, form);
      await loadSubject();
      setSavedNote(true);
    } catch (e) {
      setError(e.response?.data?.error || 'Failed to save changes.');
    } finally {
      setSaving(false);
    }
  };

  const handlePlan = async () => {
    setPlanning(true); setError(null);
    try {
      const data = await planCharacter(subjectId);
      if (data.bible) setSubject((prev) => prev ? { ...prev, bible: data.bible } : prev);
      setSamples(data.samples || []);
    } catch (e) {
      setError(e.response?.data?.error || 'Planning failed (the LLM may be offline).');
    } finally {
      setPlanning(false);
    }
  };

  const handleGenerate = async () => {
    setBusy(true); setError(null);
    try {
      await generateSamples(subjectId);
      await loadSamples();
      startPolling();
    } catch (e) {
      setError(e.response?.data?.error || 'Failed to start sample generation.');
    } finally {
      setBusy(false);
    }
  };

  const submitRegen = async () => {
    const sid = regenTarget.id;
    const body = regenPrompt.trim() ? { prompt_override: regenPrompt.trim() } : {};
    setRegenTarget(null); setRegenPrompt('');
    setError(null);
    try {
      await regenerateSample(subjectId, sid, body);
      await loadSamples();
      startPolling();
    } catch (e) {
      setError(e.response?.data?.error || 'Failed to regenerate sample.');
    }
  };

  const toggleApprove = async (sample) => {
    try {
      await approveSamples(subjectId, [sample.id], !sample.approved);
      await loadSamples();
    } catch (e) {
      setError(e.response?.data?.error || 'Failed to update approval.');
    }
  };

  const approveAllDone = async () => {
    const ids = samples.filter((s) => s.status === 'done').map((s) => s.id);
    if (!ids.length) return;
    try {
      await approveSamples(subjectId, ids, true);
      await loadSamples();
    } catch (e) {
      setError(e.response?.data?.error || 'Failed to approve set.');
    }
  };

  const handleTrain = async () => {
    setBusy(true); setError(null);
    try {
      await trainSubject(subjectId);
      await loadSubject();
      startPolling();
    } catch (e) {
      setError(e.response?.data?.error
        || (e.response?.status === 409 ? 'Already training.' : 'Failed to start training.'));
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <Box sx={{ display: 'flex', justifyContent: 'center', p: 6 }}><CircularProgress /></Box>;
  }
  if (!subject) {
    return (
      <Box sx={{ p: 3 }}>
        <Button startIcon={<ArrowBackIcon />} onClick={() => navigate('/cast')}>Back to studio</Button>
        <Alert severity="error" sx={{ mt: 2 }}>{error || 'Subject not found.'}</Alert>
      </Box>
    );
  }

  const approvedCount = samples.filter((s) => s.approved).length;
  const training = subject.training_status === 'training';

  return (
    <Box sx={{ p: { xs: 1, sm: 2 } }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <IconButton onClick={() => navigate('/cast')} aria-label="back to studio"><ArrowBackIcon /></IconButton>
        <Typography variant="h5" sx={{ fontWeight: 600 }}>{subject.name}</Typography>
        <Chip label={subject.kind} size="small" variant="outlined" />
        <Chip label={subject.training_status} size="small"
              color={subject.training_status === 'trained' ? 'success' : training ? 'warning' : 'default'} />
        {polling && <CircularProgress size={18} sx={{ ml: 1 }} />}
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Tab label="Overview" />
        <Tab label="Training Data" />
        <Tab label="Generate Character" />
        <Tab label="Versions" />
      </Tabs>

      {/* ── Overview ───────────────────────────────────────────────────────── */}
      {tab === 0 && (
        <Grid container spacing={3}>
          <Grid item xs={12} sm={4} md={3}>
            <Box sx={{ borderRadius: 1, overflow: 'hidden' }}><SubjectThumb subject={subject} /></Box>
          </Grid>
          <Grid item xs={12} sm={8} md={9}>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, maxWidth: 560 }}>
              <TextField label="Name" value={form.name}
                         onChange={(e) => setForm({ ...form, name: e.target.value })} fullWidth />
              {subject.kind === 'character' && (
                <TextField label="Trigger word (LoRA token)" value={form.trigger_word}
                           onChange={(e) => setForm({ ...form, trigger_word: e.target.value })} fullWidth
                           helperText="Rare token the LoRA trains on; every prompt must include it. Blank → uses the name." />
              )}
              <TextField label="Voice ID (optional)" value={form.voice_id}
                         onChange={(e) => setForm({ ...form, voice_id: e.target.value })} fullWidth
                         helperText="Audio Foundry voice for narration. Leave blank to clear." />
              <TextField label="Description" value={form.description} multiline rows={3}
                         onChange={(e) => setForm({ ...form, description: e.target.value })} fullWidth />
              <Box>
                <Button variant="contained" onClick={handleSave} disabled={saving || !form.name}>
                  {saving ? 'Saving…' : 'Save changes'}
                </Button>
                {savedNote && <Typography variant="caption" color="success.main" sx={{ ml: 2 }}>Saved</Typography>}
              </Box>
              {subject.bible && (
                <Box>
                  <Divider sx={{ my: 1 }} />
                  <Typography variant="overline" color="text.secondary">Identity bible (read-only)</Typography>
                  <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{subject.bible}</Typography>
                </Box>
              )}
            </Box>
          </Grid>
        </Grid>
      )}

      {/* ── Training Data ──────────────────────────────────────────────────── */}
      {tab === 1 && (
        <Box sx={{ maxWidth: 720 }}>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Reference images used to train this character's LoRA. Drop more in to expand the set.
          </Typography>
          <DragDropImageUpload
            subjectId={subject.id}
            existingPaths={subject.ref_image_paths || []}
            onUploaded={loadSubject}
            helperText="Uploads immediately to this cast member."
          />
        </Box>
      )}

      {/* ── Generate Character ─────────────────────────────────────────────── */}
      {tab === 2 && (
        <Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 2 }}>
            <Button variant="outlined" startIcon={<AutoAwesomeIcon />} onClick={handlePlan} disabled={planning || busy}>
              {planning ? 'Planning…' : subject.bible ? 'Re-plan sheet' : 'Plan reference sheet'}
            </Button>
            <Button variant="contained" startIcon={<AutoAwesomeIcon />} onClick={handleGenerate}
                    disabled={busy || planning || !samples.length}>
              Generate images
            </Button>
            <Button size="small" onClick={approveAllDone} disabled={!samples.some((s) => s.status === 'done')}>
              Approve all generated
            </Button>
            <Box sx={{ flexGrow: 1 }} />
            <Chip label={`${approvedCount}/${samples.length} approved`} size="small"
                  color={approvedCount > 0 ? 'success' : 'default'} variant="outlined" />
          </Box>

          {!samples.length ? (
            <Typography color="text.secondary" sx={{ p: 4, textAlign: 'center' }}>
              No reference sheet yet. Click <b>Plan reference sheet</b> to have the Casting Director write a
              frozen identity bible + ~32 varied shot prompts, then <b>Generate images</b>.
            </Typography>
          ) : (
            <Grid container spacing={2}>
              {samples.map((s) => (
                <Grid item xs={6} sm={4} md={3} lg={2} key={s.id}>
                  <Card variant="outlined">
                    {s.image_url ? (
                      <CardMedia component="img" height="160" image={s.image_url} alt={s.angle || `sample ${s.index}`}
                                 sx={{ objectFit: 'cover' }} />
                    ) : (
                      <Box sx={{ height: 160, display: 'flex', alignItems: 'center', justifyContent: 'center',
                                 bgcolor: 'action.hover' }}>
                        {isPending(s) ? <CircularProgress size={22} />
                          : <Typography variant="caption" color="text.secondary">{s.status}</Typography>}
                      </Box>
                    )}
                    <CardContent sx={{ py: 1 }}>
                      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 0.5 }}>
                        <Tooltip title={s.image_prompt || ''}>
                          <Typography variant="caption" noWrap>{s.angle || `Shot ${s.index + 1}`}</Typography>
                        </Tooltip>
                        <StatusChip status={s.status} />
                      </Box>
                    </CardContent>
                    <CardActions sx={{ pt: 0, justifyContent: 'space-between' }}>
                      <Tooltip title={s.approved ? 'Approved — click to un-approve' : 'Approve this sample'}>
                        <IconButton size="small" color={s.approved ? 'success' : 'default'}
                                    onClick={() => toggleApprove(s)} aria-label="toggle approval">
                          {s.approved ? <CheckCircleIcon fontSize="small" /> : <RadioButtonUncheckedIcon fontSize="small" />}
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Regenerate this sample">
                        <span>
                          <IconButton size="small" onClick={() => { setRegenTarget(s); setRegenPrompt(s.image_prompt || ''); }}
                                      disabled={isPending(s)} aria-label="regenerate sample">
                            <RefreshIcon fontSize="small" />
                          </IconButton>
                        </span>
                      </Tooltip>
                    </CardActions>
                  </Card>
                </Grid>
              ))}
            </Grid>
          )}

          <Divider sx={{ my: 3 }} />
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <Button variant="contained" color="secondary" onClick={handleTrain}
                    disabled={busy || training || approvedCount === 0}>
              {training ? 'Training…' : 'Train LoRA'}
            </Button>
            <Typography variant="caption" color="text.secondary">
              {approvedCount === 0
                ? 'Approve at least one sample to train.'
                : `Trains on the ${approvedCount} approved sample${approvedCount > 1 ? 's' : ''}. ~hours on a 16GB GPU; runs in the background.`}
            </Typography>
          </Box>
        </Box>
      )}

      {/* ── Versions ───────────────────────────────────────────────────────── */}
      {tab === 3 && (
        <Box sx={{ maxWidth: 560 }}>
          <Typography variant="subtitle2" gutterBottom>Trained LoRA</Typography>
          <Typography variant="body2">Status: <b>{subject.training_status}</b></Typography>
          <Typography variant="body2">Version: {subject.lora_version || 0}</Typography>
          <Typography variant="body2" sx={{ wordBreak: 'break-all' }}>
            Path: {subject.lora_path || <em>none yet</em>}
          </Typography>
          {subject.training_status === 'trained' && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="body2" color="text.secondary">Use this character in:</Typography>
              <Box sx={{ display: 'flex', gap: 2, mt: 0.5 }}>
                <Link component="button" onClick={() => navigate(`/images?character=${subject.id}`)}>Images →</Link>
                <Link component="button" onClick={() => navigate('/music-video')}>Music Video →</Link>
                <Link component="button" onClick={() => navigate('/video')}>Video Gen →</Link>
              </Box>
            </Box>
          )}
        </Box>
      )}

      {/* Regenerate dialog */}
      <Dialog open={!!regenTarget} onClose={() => setRegenTarget(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Regenerate sample</DialogTitle>
        <DialogContent>
          <TextField label="Prompt override (optional)" value={regenPrompt}
                     onChange={(e) => setRegenPrompt(e.target.value)} fullWidth multiline rows={4} sx={{ mt: 1 }}
                     helperText="Tweak the prompt to fix an off-model sample. Leave as-is to just re-roll the seed." />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRegenTarget(null)}>Cancel</Button>
          <Button variant="contained" onClick={submitRegen}>Regenerate</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default CastMemberPage;
