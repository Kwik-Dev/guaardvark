import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useUnifiedProgress } from '../contexts/UnifiedProgressContext';
import {
  Box, Tabs, Tab, Typography, Button, TextField, Card, CardMedia, CardContent,
  CardActions, Chip, CircularProgress, Alert, Dialog, DialogTitle, DialogContent,
  DialogActions, Grid, IconButton, Tooltip, Divider, Link, LinearProgress,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import RefreshIcon from '@mui/icons-material/Refresh';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked';
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import CloseIcon from '@mui/icons-material/Close';
import {
  getCastSubject, getCastSubjectDetail, updateCastSubject, planCharacter, generateSamples,
  listSamples, regenerateSample, approveSamples, deleteSample, trainSubject,
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
  const [lightboxIdx, setLightboxIdx] = useState(null); // open enlarged viewer at this samples[] index

  const loadSubject = useCallback(async () => {
    // Prefer efficient single-subject (with samples when convenient).
    // Falls back gracefully.
    try {
      const detail = await getCastSubjectDetail(subjectId, { includeSamples: true });
      const s = detail?.subject || detail;
      setSubject(s);
      if (s) setForm({
        name: s.name || '', description: s.description || '',
        trigger_word: s.trigger_word || '', voice_id: s.voice_id || '',
      });
      if (detail?.samples) {
        setSamples(detail.samples);
      }
      return s;
    } catch (e) {
      // legacy fallback
      const s = await getCastSubject(subjectId);
      setSubject(s);
      if (s) setForm({
        name: s.name || '', description: s.description || '',
        trigger_word: s.trigger_word || '', voice_id: s.voice_id || '',
      });
      return s;
    }
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
        const rows = await loadSamples();
        // If a generation/regeneration is already in flight when we land on the
        // page (e.g. after a refresh), auto-resume polling so finished images
        // appear without a manual reload.
        if (alive && ((rows || []).some(isPending) || s?.training_status === 'training')) {
          pollCount.current = 0;
          setPolling(true);
        }
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

  // React to unified progress jobs for *this* subject (key for batch queue of many cast trainings).
  // The backend now creates processes with additional_data.subject_id on dispatch.
  // This lets the page get live updates / terminal notifications without sole reliance on 5s poll,
  // enabling long-running day+ batch jobs to surface correctly.
  const { activeProcesses } = useUnifiedProgress();
  useEffect(() => {
    if (!subjectId) return;
    const procs = Array.from(activeProcesses.values());
    const match = procs.find((p) => {
      const ad = p.additional_data || p.metadata || p;
      return String(ad.subject_id || ad.sample_subject_id || '') === String(subjectId);
    });
    if (match) {
      const st = (match.status || '').toLowerCase();
      if (['complete', 'end', 'error', 'cancelled', 'failed'].includes(st)) {
        // terminal — refresh authoritative cast state
        loadSubject();
        loadSamples();
        setPolling(false);
      } else if (match.progress != null) {
        // optional: could surface unified % alongside local progress bar
      }
    }
  }, [activeProcesses, subjectId, loadSubject, loadSamples]);

  // Arrow-key / Escape navigation for the enlarged image viewer.
  useEffect(() => {
    if (lightboxIdx === null) return undefined;
    const onKey = (e) => {
      if (e.key === 'ArrowLeft') setLightboxIdx((i) => (i > 0 ? i - 1 : i));
      else if (e.key === 'ArrowRight') setLightboxIdx((i) => (i < samples.length - 1 ? i + 1 : i));
      else if (e.key === 'Escape') setLightboxIdx(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [lightboxIdx, samples.length]);

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
      const res = await generateSamples(subjectId);
      await loadSamples();
      if (res?.job_id) {
        console.debug('[CastMemberPage] generate job', res.job_id);
      }
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
      const res = await regenerateSample(subjectId, sid, body);
      await loadSamples();
      if (res?.job_id) console.debug('[CastMemberPage] regen job', res.job_id);
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

  const handleDeleteSample = async (sample) => {
    try {
      await deleteSample(subjectId, sample.id);
      // Keep the lightbox sane if the open image was the one removed.
      setLightboxIdx((i) => (i === null ? i : Math.max(0, Math.min(i, samples.length - 2))));
      await loadSamples();
    } catch (e) {
      setError(e.response?.data?.error || 'Failed to delete sample.');
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
      const res = await trainSubject(subjectId); // now may include job_id
      await loadSubject();
      if (res?.job_id) {
        // job is now in unified system; the useUnifiedProgress effect above will react
        console.debug('[CastMemberPage] train job started', res.job_id);
      }
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
  const doneCount = samples.filter((s) => s.status === 'done').length;
  const generatingCount = samples.filter((s) => s.status === 'generating').length;
  const failedCount = samples.filter((s) => s.status === 'failed').length;
  const total = samples.length;
  const active = generatingCount > 0 || polling;
  const refCount = (subject.ref_image_paths || []).length;
  // Trainable from EITHER uploaded reference images (the primary Step-1 flow) OR
  // approved generated samples (the no-images fallback) — the backend trains on
  // the union of both.
  const trainable = refCount > 0 || approvedCount > 0;
  const training = subject.training_status === 'training';

  return (
    <Box sx={{ p: { xs: 1, sm: 2 } }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <IconButton onClick={() => navigate('/cast')} aria-label="back to studio"><ArrowBackIcon /></IconButton>
        <Typography variant="h5" sx={{ fontWeight: 600 }}>{subject.name}</Typography>
        <Chip label={subject.kind} size="small" variant="outlined" />
        <Chip label={subject.training_status} size="small"
              color={
                subject.training_status === 'trained' ? 'success' :
                subject.training_status === 'failed' ? 'error' :
                training ? 'warning' : 'default'
              } />
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
            Images that will be used for the *next* training run (uploaded refs + any approved generated samples).
            The current LoRA version was trained on a previous snapshot of this set.
            Add new outfits / details then click Train to amend.
          </Typography>
          <DragDropImageUpload
            subjectId={subject.id}
            existingPaths={subject.ref_image_paths || []}
            onUploaded={loadSubject}
            helperText="Uploads immediately to this cast member."
          />

          <Divider sx={{ my: 3 }} />
          <Typography variant="subtitle2" gutterBottom>Train the LoRA</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {refCount > 0
              ? `${refCount} reference image${refCount > 1 ? 's' : ''} ready${approvedCount > 0 ? ` + ${approvedCount} approved generated` : ''}.`
              : approvedCount > 0
                ? `No uploads yet — will train on ${approvedCount} approved generated sample${approvedCount > 1 ? 's' : ''}.`
                : 'Drop in reference images above (or generate + approve some in the Generate Character tab) to enable training.'}
            {' '}Training incorporates the current set. Add new data (outfits, details) later and Train again to amend/evolve.
          </Typography>
          <Button variant="contained" color="secondary" onClick={handleTrain}
                  disabled={busy || training || !trainable}>
            {training ? 'Training…' : 'Train LoRA'}
          </Button>
          {subject.training_status && subject.training_status !== 'untrained' && (
            <Typography variant="caption" color="text.secondary" sx={{ ml: 2 }}>
              status: {subject.training_status}
            </Typography>
          )}

          {/* Enhanced error + recovery for real hardware (RTX 4070 Ti SUPER etc.)
              Matches user's "no simulations" requirement and the exact failure mode
              seen when the venv-torch cannot see CUDA despite a working GPU. */}
          {subject.training_status === 'failed' && subject.training_error && (
            <Alert severity="error" sx={{ mt: 1.5 }}>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                Training failed
              </Typography>
              <Typography
                variant="body2"
                component="pre"
                sx={{ 
                  whiteSpace: 'pre-wrap', 
                  fontFamily: 'monospace', 
                  fontSize: '0.8rem',
                  mt: 0.5,
                  mb: 0.5
                }}
              >
                {subject.training_error}
              </Typography>
              <Typography variant="caption">
                Fix the problem, then click “Train LoRA” again. New training data (additional outfits etc.) will be incorporated on the next run (amend).
              </Typography>
            </Alert>
          )}
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

          {/* Progress + honest status — so a planned-but-not-generated sheet doesn't
              read as "stuck", and a real render shows how far along it is. */}
          {total > 0 && (
            <Box sx={{ mb: 2 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography variant="caption" color="text.secondary">
                  {active
                    ? `Generating… ${doneCount}/${total} done`
                    : doneCount === 0
                      ? `Planned ${total} shots — click "Generate images" to render them.`
                      : doneCount < total
                        ? `${doneCount}/${total} rendered${failedCount ? ` · ${failedCount} failed` : ''} — click "Generate images" to finish.`
                        : `All ${total} rendered.`}
                </Typography>
                <Typography variant="caption" color="text.secondary">{doneCount}/{total}</Typography>
              </Box>
              <LinearProgress
                variant={active && doneCount === 0 ? 'indeterminate' : 'determinate'}
                value={total ? (doneCount / total) * 100 : 0}
              />
            </Box>
          )}

          {!samples.length ? (
            <Typography color="text.secondary" sx={{ p: 4, textAlign: 'center' }}>
              No reference sheet yet. Click <b>Plan reference sheet</b> to have the Casting Director write a
              frozen identity bible + ~32 varied shot prompts, then <b>Generate images</b>.
            </Typography>
          ) : (
            <Box sx={{ maxHeight: '60vh', overflowY: 'auto', pr: 1, mx: -0.5, px: 0.5 }}>
            <Grid container spacing={2}>
              {samples.map((s, idx) => (
                <Grid item xs={6} sm={4} md={3} lg={2} key={s.id}>
                  <Card variant="outlined">
                    {s.image_url ? (
                      <CardMedia component="img" height="160" image={s.image_url} alt={s.angle || `sample ${s.index}`}
                                 onClick={() => setLightboxIdx(idx)}
                                 sx={{ objectFit: 'cover', cursor: 'zoom-in' }} />
                    ) : (
                      <Box sx={{ height: 160, display: 'flex', alignItems: 'center', justifyContent: 'center',
                                 bgcolor: 'action.hover' }}>
                        {s.status === 'generating' ? <CircularProgress size={22} />
                          : s.status === 'pending' ? <Typography variant="caption" color="text.disabled">queued</Typography>
                          : s.status === 'failed' ? <Typography variant="caption" color="error">failed</Typography>
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
                      <Box>
                        <Tooltip title="Regenerate this sample">
                          <span>
                            <IconButton size="small" onClick={() => { setRegenTarget(s); setRegenPrompt(s.image_prompt || ''); }}
                                        disabled={isPending(s)} aria-label="regenerate sample">
                              <RefreshIcon fontSize="small" />
                            </IconButton>
                          </span>
                        </Tooltip>
                        <Tooltip title="Remove this generation">
                          <IconButton size="small" onClick={() => handleDeleteSample(s)} aria-label="remove sample">
                            <CloseIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </Box>
                    </CardActions>
                  </Card>
                </Grid>
              ))}
            </Grid>
            </Box>
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

      {/* Lightbox — click a thumbnail to enlarge; ←/→ or buttons to step, Esc to close. */}
      {lightboxIdx !== null && samples[lightboxIdx] && (
        <Dialog open onClose={() => setLightboxIdx(null)} maxWidth="lg" fullWidth
                PaperProps={{ sx: { bgcolor: 'grey.900' } }}>
          <DialogContent sx={{ position: 'relative', p: 0, display: 'flex', alignItems: 'center',
                                justifyContent: 'center', minHeight: '70vh', bgcolor: 'black' }}>
            {samples[lightboxIdx].image_url ? (
              <Box component="img" src={samples[lightboxIdx].image_url}
                   alt={samples[lightboxIdx].angle || `sample ${lightboxIdx + 1}`}
                   sx={{ maxWidth: '100%', maxHeight: '82vh', objectFit: 'contain', display: 'block' }} />
            ) : (
              <Typography color="grey.500">{samples[lightboxIdx].status}</Typography>
            )}
            <IconButton onClick={() => setLightboxIdx((i) => Math.max(0, i - 1))} disabled={lightboxIdx === 0}
                        sx={{ position: 'absolute', left: 8, color: 'white', bgcolor: 'rgba(0,0,0,0.45)',
                              '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' } }} aria-label="previous">
              <ChevronLeftIcon />
            </IconButton>
            <IconButton onClick={() => setLightboxIdx((i) => Math.min(samples.length - 1, i + 1))}
                        disabled={lightboxIdx === samples.length - 1}
                        sx={{ position: 'absolute', right: 8, color: 'white', bgcolor: 'rgba(0,0,0,0.45)',
                              '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' } }} aria-label="next">
              <ChevronRightIcon />
            </IconButton>
            <IconButton onClick={() => setLightboxIdx(null)}
                        sx={{ position: 'absolute', top: 8, right: 8, color: 'white', bgcolor: 'rgba(0,0,0,0.45)',
                              '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' } }} aria-label="close">
              <CloseIcon />
            </IconButton>
          </DialogContent>
          <DialogActions sx={{ justifyContent: 'space-between', bgcolor: 'grey.900' }}>
            <Tooltip title={samples[lightboxIdx].image_prompt || ''}>
              <Typography variant="caption" color="grey.400" noWrap sx={{ px: 1, maxWidth: '70%' }}>
                {lightboxIdx + 1}/{samples.length} · {samples[lightboxIdx].angle || `Shot ${lightboxIdx + 1}`}
              </Typography>
            </Tooltip>
            <Box>
              <Button size="small" startIcon={<CloseIcon />} color="inherit"
                      onClick={() => handleDeleteSample(samples[lightboxIdx])}>
                Remove
              </Button>
              <Button size="small" startIcon={samples[lightboxIdx].approved ? <CheckCircleIcon /> : <RadioButtonUncheckedIcon />}
                      onClick={() => toggleApprove(samples[lightboxIdx])}
                      color={samples[lightboxIdx].approved ? 'success' : 'inherit'}>
                {samples[lightboxIdx].approved ? 'Approved' : 'Approve'}
              </Button>
            </Box>
          </DialogActions>
        </Dialog>
      )}
    </Box>
  );
};

export default CastMemberPage;
