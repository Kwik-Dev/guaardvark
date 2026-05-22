import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Typography,
  Card,
  CardContent,
  CardActions,
  Button,
  Chip,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
  CircularProgress,
  Alert
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import AddIcon from '@mui/icons-material/Add';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import { listCastLibrary, createCastSubject, deleteCastSubject } from '../../api/productionService';
import DragDropImageUpload from './DragDropImageUpload';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

// Thumbnail with graceful fallback to the subject's initials when no ref image
// is on disk (preview endpoint 404s).
const SubjectThumb = ({ subject }) => {
  const [failed, setFailed] = useState(false);
  const initials = (subject.name || '?').split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
  return (
    <Box sx={{ position: 'relative', width: '100%', height: 160, bgcolor: 'action.hover',
               display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
      <Typography variant="h4" color="text.disabled">{initials}</Typography>
      {!failed && (
        <Box component="img" src={`${API_BASE}/cast-library/subjects/${subject.id}/preview`}
             onError={() => setFailed(true)} alt={subject.name}
             sx={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' }} />
      )}
    </Box>
  );
};

const KINDS = ['character', 'environment', 'prop'];

const CastLibraryView = () => {
  const [subjects, setSubjects] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: '', kind: 'character', description: '', trigger_word: '' });
  const [submitting, setSubmitting] = useState(false);
  const uploaderRef = useRef(null);
  const navigate = useNavigate();

  // "Generate" jumps to the Images page with this character pre-cast.
  const handleGenerate = (subject) => navigate(`/images?character=${subject.id}`);

  useEffect(() => {
    loadLibrary();
  }, []);

  const loadLibrary = async () => {
    setLoading(true);
    try {
      const data = await listCastLibrary();
      setSubjects(data.subjects || []);
    } catch (err) {
      setError('Failed to load cast library');
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    setSubmitting(true);
    try {
      // Two-phase: create the row first, then flush any staged drag-drop
      // images straight into /upload-refs against the new id.
      const subj = await createCastSubject(form);
      if (uploaderRef.current?.hasStagedFiles()) {
        await uploaderRef.current.flushTo(subj.id);
      }
      setOpen(false);
      setForm({ name: '', kind: 'character', description: '', trigger_word: '' });
      loadLibrary();
    } catch (err) {
      setError('Failed to create subject');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id) => {
    if (window.confirm('Are you sure you want to delete this subject?')) {
      try {
        await deleteCastSubject(id);
        loadLibrary();
      } catch (err) {
        setError('Failed to delete subject');
      }
    }
  };

  return (
    <Box sx={{ p: 3 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 3 }}>
        <Typography variant="h5">Cast Library</Typography>
        <Button 
          variant="contained" 
          startIcon={<AddIcon />}
          onClick={() => setOpen(true)}
        >
          New Subject
        </Button>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 5 }}>
          <CircularProgress />
        </Box>
      ) : subjects.length === 0 ? (
        <Typography color="text.secondary" sx={{ textAlign: 'center', p: 5 }}>
          No subjects in the library yet. Click "New Subject" to add one.
        </Typography>
      ) : (
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 2 }}>
          {subjects.map((s) => (
            <Card key={s.id} variant="outlined" sx={{ display: 'flex', flexDirection: 'column' }}>
              <SubjectThumb subject={s} />
              <CardContent sx={{ flexGrow: 1, pb: 1 }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 1 }}>
                  <Typography variant="subtitle1" noWrap sx={{ fontWeight: 'bold' }}>{s.name}</Typography>
                  <IconButton size="small" onClick={() => handleDelete(s.id)} color="inherit" aria-label="remove from cast library">
                    <CloseIcon fontSize="small" />
                  </IconButton>
                </Box>
                <Box sx={{ display: 'flex', gap: 0.5, mt: 0.5, flexWrap: 'wrap' }}>
                  <Chip label={s.kind} size="small" variant="outlined" color={s.kind === 'character' ? 'primary' : 'secondary'} />
                  <Chip label={s.training_status} size="small" color={s.training_status === 'trained' ? 'success' : (s.training_status === 'training' ? 'warning' : 'default')} />
                </Box>
                {s.trigger_word && (
                  <Typography variant="caption" sx={{ display: 'block', mt: 0.5, fontFamily: 'monospace' }} color="text.secondary">
                    {s.trigger_word}
                  </Typography>
                )}
              </CardContent>
              <CardActions sx={{ pt: 0 }}>
                <Button
                  size="small"
                  startIcon={<AutoAwesomeIcon />}
                  disabled={s.training_status !== 'trained'}
                  onClick={() => handleGenerate(s)}
                >
                  {s.training_status === 'trained' ? 'Generate' : 'Not trained'}
                </Button>
              </CardActions>
            </Card>
          ))}
        </Box>
      )}

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Add to Cast Library</DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
            <TextField 
              label="Name" 
              fullWidth 
              value={form.name} 
              onChange={e => setForm({...form, name: e.target.value})}
            />
            {form.kind === 'character' && (
              <TextField
                label="Trigger word (LoRA token)"
                fullWidth
                value={form.trigger_word}
                onChange={e => setForm({...form, trigger_word: e.target.value})}
                helperText="Rare token the LoRA trains on and every prompt must use (e.g. sage_harlow). Defaults to the name if blank."
              />
            )}
            <TextField
              select
              label="Kind"
              value={form.kind}
              onChange={e => setForm({...form, kind: e.target.value})}
              fullWidth
            >
              {KINDS.map((option) => (
                <MenuItem key={option} value={option}>
                  {option}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              label="Description"
              fullWidth
              multiline
              rows={2}
              value={form.description}
              onChange={e => setForm({...form, description: e.target.value})}
            />
            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                Reference images (optional — drop them here, no path-typing)
              </Typography>
              <DragDropImageUpload
                ref={uploaderRef}
                helperText="Will upload after the subject is saved."
              />
            </Box>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => { setOpen(false); setForm({ name: '', kind: 'character', description: '', trigger_word: '' }); }}>
            Cancel
          </Button>
          <Button onClick={handleCreate} variant="contained" disabled={submitting || !form.name}>
            {submitting ? 'Saving...' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default CastLibraryView;
