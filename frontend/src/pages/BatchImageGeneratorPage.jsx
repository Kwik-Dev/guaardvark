// frontend/src/pages/BatchImageGeneratorPage.jsx
// Batch Image Generator - Mass image generation with progress tracking
// Integrates with unified progress system and real-time updates

import React, { useState, useCallback, useEffect, useRef, useMemo } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Button,
  TextField,
  Grid,
  Chip,
  LinearProgress,
  Alert,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Slider,
  Switch,
  FormControlLabel,
  Paper,
  ImageList,
  ImageListItem,
  ImageListItemBar,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  useTheme,
  useMediaQuery,
  Stack,
  Tooltip,
} from '@mui/material';
import {
  ExpandMore,
  Upload,
  PlayArrow,
  Download,
  GetApp,
  Visibility,
  Cancel,
  Close as CloseIcon,
  Settings as SettingsIcon,
  Refresh as RefreshIcon,
  Image as ImageIcon,
} from '@mui/icons-material';

import { useUnifiedProgress } from '../contexts/UnifiedProgressContext';
import { useSearchParams } from 'react-router-dom';
import PageLayout from '../components/layout/PageLayout';
import CharacterPicker from '../components/filmcrew/CharacterPicker';
import ImageLightbox from '../components/images/ImageLightbox';
import GpuGateBanner from '../components/common/GpuGateBanner';
import useJobsGate from '../hooks/useJobsGate';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

const encodeFilename = (filename) => {
  if (!filename) return '';
  return filename.split('/').map(part => encodeURIComponent(part)).join('/');
};

const getFilenameFromPath = (path) => {
  if (!path) return null;
  return path.replace(/\\/g, '/').split('/').pop();
};

const mapBatchResultsToImages = (batchStatus) => {
  if (!batchStatus?.results) return [];
  return batchStatus.results
    .filter(r => r.success && r.image_path)
    .map(r => ({
      id: r.prompt_id,
      path: r.image_path,
      thumbnail: r.thumbnail_path,
      imageFilename: getFilenameFromPath(r.image_path),
      thumbnailFilename: getFilenameFromPath(r.thumbnail_path),
      prompt: r.metadata?.original_prompt || '',
      metadata: r.metadata,
      batchId: batchStatus.batch_id,
    }));
};

const POLLABLE_STATUSES = new Set(['queued', 'pending', 'running']);
const TERMINAL_BATCH_STATUSES = new Set(['completed', 'error', 'cancelled']);

const debugLog = (...args) => {
  if (import.meta.env.DEV) {
    console.debug(...args);
  }
};

// Utility function to sanitize text for display
const sanitizeText = (text) => {
  if (!text) return '';
  return text.replace(/[<>&"]/g, (match) => {
    const escape = {
      '<': '&lt;',
      '>': '&gt;',
      '&': '&amp;',
      '"': '&quot;'
    };
    return escape[match];
  });
};

const BatchImageGeneratorPage = ({ embedded = false }) => {
  const theme = useTheme();
  const [searchParams] = useSearchParams();
  const isXs = useMediaQuery(theme.breakpoints.down('sm'));
  const isSm = useMediaQuery(theme.breakpoints.between('sm', 'md'));

  // Calculate responsive columns for ImageList
  const imageListCols = isXs ? 2 : isSm ? 3 : 4;

  // State management
  const [inputMode, setInputMode] = useState('single'); // 'single' (default, whole text as one prompt), 'bulk', 'csv', or 'blueprint'
  const [batchItems, setBatchItems] = useState(''); // Bulk textarea input like FileGenerationPage
  const [lookAndFeel, setLookAndFeel] = useState(''); // Style/aesthetic to apply to all prompts
  const [csvFile, setCsvFile] = useState(null);
  const [blueprintFile, setBlueprintFile] = useState(null);
  const [quantity, setQuantity] = useState(1); // Number of images to generate
  const [activeBatch, setActiveBatch] = useState(null);
  const [batchHistory, setBatchHistory] = useState([]);
  const [clearedBatchIds, setClearedBatchIds] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem('clearedImageBatchIds') || '[]');
    } catch (e) {
      return [];
    }
  });
  const [generatedImages, setGeneratedImages] = useState([]);
  const [lightboxImage, setLightboxImage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [showPromptPreview, setShowPromptPreview] = useState(false);
  // Live queue panel (mirrors Video Gen) — stacked batches drain one-at-a-time
  const [queue, setQueue] = useState([]);
  const { gpuBusy, blockReason } = useJobsGate({ submitMode: 'queue' });

  // Director intelligence (new) — expand high-level concept via media_director (same as MV)
  const [directorEnabled, setDirectorEnabled] = useState(false);
  const [directorGuidance, setDirectorGuidance] = useState("");
  const [isExpanding, setIsExpanding] = useState(false);

  // New: Content presets and quality enhancement state
  const [contentPresets, setContentPresets] = useState({});
  const [selectedPreset, setSelectedPreset] = useState('auto'); // 'auto' = auto-detect
  const [autoEnhance, setAutoEnhance] = useState(true);
  const [enhanceAnatomy, setEnhanceAnatomy] = useState(true);
  const [enhanceFaces, setEnhanceFaces] = useState(true);
  const [enhanceHands, setEnhanceHands] = useState(true);
  const [_contentDetection, setContentDetection] = useState(null);
  const [_analyzingPrompt, setAnalyzingPrompt] = useState(false);
  // Character casting: selected subject ids whose LoRA + trigger get applied.
  const [castSubjectIds, setCastSubjectIds] = useState([]);

  // Pre-cast a character when arriving from the Cast Library "Generate" button
  // (/images?character=<id>).
  useEffect(() => {
    const cid = searchParams.get('character');
    if (cid) setCastSubjectIds([parseInt(cid, 10)]);
  }, [searchParams]);

  // Generation parameters
  const [params, setParams] = useState({
    model: 'auto',
    style: 'realistic',
    quality_preset: 'standard',
    // Modern family defaults (zimage / auto) — HF Turbo recipe 9/0, not SD-era 512/20/7.5
    width: 1024,
    height: 1024,
    steps: 9,
    guidance: 0.0,
    max_workers: 2,
    preserve_order: true,
    generate_thumbnails: true,
    save_metadata: true
  });

  // Refs
  const fileInputRef = useRef(null);
  const blueprintFileInputRef = useRef(null);
  const pollingRef = useRef(null);
  // Tracks which batch polling is for; survives interval teardown so in-flight fetches
  // still apply (pollingRef is cleared on cleanup and must not gate result application).
  const pollingBatchIdRef = useRef(null);

  // Progress system integration
  const { activeProcesses } = useUnifiedProgress();

  // Style options
  const styleOptions = [
    { value: 'realistic', label: 'Realistic' },
    { value: 'artistic', label: 'Artistic' },
    { value: 'cartoon', label: 'Cartoon' },
    { value: 'sketch', label: 'Sketch' },
    { value: 'infographic', label: 'Infographic' },
    { value: 'technical', label: 'Technical' }
  ];

  // Model options — fetched from the backend (/batch-image/models) so the list
  // never drifts from the canonical catalog. "Auto" routes per-prompt.
  const AUTO_MODEL_OPTION = {
    value: 'auto',
    label: 'Auto — best per prompt ⭐',
    description: 'Router picks the best downloaded model for each prompt',
  };
  const [modelOptions, setModelOptions] = useState([AUTO_MODEL_OPTION]);

  useEffect(() => {
    (async () => {
      try {
        const response = await fetch(`${API_BASE}/batch-image/models`);
        const data = await response.json();
        if (data.success && data.data?.models) {
          const fetched = data.data.models.map(m => ({
            value: m.id,
            label: m.recommended ? `${m.label} ⭐` : m.label,
            description: m.description || '',
          }));
          setModelOptions([AUTO_MODEL_OPTION, ...fetched]);
        }
      } catch (e) {
        debugLog('Failed to load image models', e);
      }
    })();
  }, []);

  // Quality presets — family-aware (zimage/krea turbo-safe + FLUX + SDXL + classic SD)
  const modelKey = (params.model || 'auto').toLowerCase();
  const isFlux = modelKey.includes('flux');
  const isZimage = modelKey.includes('zimage') || modelKey.includes('z-image') || modelKey === 'auto';
  const isKreaRaw = modelKey.includes('krea') && modelKey.includes('raw');
  const isKreaTurbo = modelKey.includes('krea') && !isKreaRaw;
  const isSdxl = modelKey.includes('xl') || modelKey.includes('sdxl');
  const qualityPresets = isFlux
    ? [
        { value: 'flux-fast', label: 'FLUX Fast', steps: 16, guidance: 3.0, description: 'Faster FLUX.1-dev stills' },
        { value: 'flux-quality', label: 'FLUX Max Quality', steps: 28, guidance: 3.5, description: 'Default max-quality FLUX.1-dev' },
        { value: 'flux-ultra', label: 'FLUX Ultra', steps: 40, guidance: 4.0, description: 'Highest steps — slow, peak detail' },
      ]
    : isZimage
      ? [
          { value: 'fast', label: 'Fast', steps: 6, guidance: 0.0, description: 'Quick draft' },
          { value: 'standard', label: 'Standard', steps: 9, guidance: 0.0, description: 'Official Turbo recipe (HF)' },
          // 2026-08-04: 'high' used to SILENTLY rewrite the canvas to 2048×2048 —
          // users hit 16GB memory crashes without ever choosing 2K. The 2K jump
          // is now its own explicitly-labeled preset.
          { value: 'high-2k', label: 'High 2K (2048²)', steps: 9, guidance: 0.0, description: 'Official recipe at 2K canvas — heavy; 16GB cards may refuse' },
        ]
      : isKreaTurbo
        ? [
            { value: 'fast', label: 'Fast', steps: 6, guidance: 0.0, description: 'Krea turbo CFG-free' },
            { value: 'standard', label: 'Standard', steps: 8, guidance: 0.0, description: 'Balanced turbo' },
            { value: 'high', label: 'High Quality', steps: 12, guidance: 0.0, description: 'More steps' },
          ]
        : isKreaRaw
          ? [
              { value: 'standard', label: 'Standard', steps: 40, guidance: 3.5, description: 'Krea raw quality' },
              { value: 'high', label: 'High Quality', steps: 52, guidance: 3.5, description: 'Default raw' },
              { value: 'ultra', label: 'Ultra', steps: 60, guidance: 3.5, description: 'Slow, peak detail' },
            ]
          : isSdxl
            ? [
                { value: 'fast', label: 'Fast', steps: 20, guidance: 6.0, description: 'Quick SDXL' },
                { value: 'standard', label: 'Standard', steps: 25, guidance: 7.0, description: 'Balanced SDXL' },
                { value: 'high', label: 'High Quality', steps: 35, guidance: 7.5, description: 'Final SDXL' },
              ]
            : [
                { value: 'fast', label: 'Fast', steps: 15, guidance: 7.0, description: 'Quick generation, good for testing' },
                { value: 'standard', label: 'Standard', steps: 20, guidance: 7.5, description: 'Balanced quality and speed' },
                { value: 'high', label: 'High Quality', steps: 30, guidance: 8.0, description: 'High quality, slower generation' },
              ];

  // Dimension presets — base + model-family 2K / Flux~2MP packs (filtered below)
  const dimensionPresetsBase = [
    // SD 1.5 / Standard presets
    { label: 'Square (512x512)', width: 512, height: 512, pack: 'legacy' },
    { label: 'Portrait (512x768)', width: 512, height: 768, pack: 'legacy' },
    { label: 'Landscape (768x512)', width: 768, height: 512, pack: 'legacy' },
    { label: 'Large Square (768x768)', width: 768, height: 768, pack: 'legacy' },
    { label: 'HD Portrait (512x1024)', width: 512, height: 1024, pack: 'legacy' },
    { label: 'HD Landscape (1024x512)', width: 1024, height: 512, pack: 'legacy' },
    // 1K / SDXL-class (default daily driver for modern models)
    { label: '1K Square (1024x1024)', width: 1024, height: 1024, pack: '1k' },
    { label: '1K Portrait (832x1216)', width: 832, height: 1216, pack: '1k' },
    { label: '1K Landscape (1216x832)', width: 1216, height: 832, pack: '1k' },
    { label: '1K Wide 16:9 (1344x768)', width: 1344, height: 768, pack: '1k' },
    { label: '1K Tall 9:16 (768x1344)', width: 768, height: 1344, pack: '1k' },
    // Z-Image / Krea 2K pack (area ≤ ~2048²; long side up to 2688)
    { label: '2K Square (2048x2048)', width: 2048, height: 2048, pack: '2k' },
    { label: '2K Landscape 16:9 (2688x1472)', width: 2688, height: 1472, pack: '2k' },
    { label: '2K Portrait 9:16 (1472x2688)', width: 1472, height: 2688, pack: '2k' },
    { label: '2K Landscape 3:2 (2496x1664)', width: 2496, height: 1664, pack: '2k' },
    { label: '2K Portrait 2:3 (1664x2496)', width: 1664, height: 2496, pack: '2k' },
    // FLUX.1-dev max (~2.0 MP design range — NOT 2048²)
    { label: 'Flux max square (1408x1408)', width: 1408, height: 1408, pack: 'flux2mp' },
    { label: 'Flux max 16:9 (1920x1088)', width: 1920, height: 1088, pack: 'flux2mp' },
    { label: 'Flux max 9:16 (1088x1920)', width: 1088, height: 1920, pack: 'flux2mp' },
    { label: 'Flux max 3:2 (1728x1152)', width: 1728, height: 1152, pack: 'flux2mp' },
    { label: 'Flux max 2:3 (1152x1728)', width: 1152, height: 1728, pack: 'flux2mp' },
  ];

  const dimensionPresetsForModel = (modelValue) => {
    const m = String(modelValue || 'auto');
    const isFlux = m.startsWith('flux');
    const isModernDit =
      m === 'auto' || m === 'zimage-turbo' || m.startsWith('krea2') || m === 'zimage';
    const isLegacySd =
      m === 'realistic-vision' || m === 'epic-realism' || m === 'sd-1.5';
    return dimensionPresetsBase.filter((p) => {
      // Draft 512/768 sizes available for Turbo/Krea as well as classic SD / auto
      if (p.pack === 'legacy') return isLegacySd || m === 'auto' || isModernDit;
      if (p.pack === '1k') return !isLegacySd || m === 'auto';
      if (p.pack === '2k') return isModernDit && !isFlux;
      if (p.pack === 'flux2mp') return isFlux;
      return true;
    });
  };

  const dimensionPresets = useMemo(
    () => dimensionPresetsForModel(params.model),
    // params.model only — forModel is pure over modelValue
    [params.model],
  );

  // Analyze current prompt for content detection
  const analyzeCurrentPrompt = useCallback(async (prompt) => {
    if (!prompt) return;

    setAnalyzingPrompt(true);
    try {
      const response = await fetch(`${API_BASE}/batch-image/analyze-prompt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt })
      });

      if (!response.ok) return;

      const data = await response.json();
      if (data.success && data.data.detection) {
        setContentDetection(data.data.detection);

        // If auto-detect mode, apply recommended settings
        if (selectedPreset === 'auto' && data.data.detection.recommended_preset) {
          const preset = contentPresets[data.data.detection.recommended_preset];
          if (preset) {
            setParams(prev => ({
              ...prev,
              steps: preset.recommended_steps || prev.steps,
              guidance: preset.recommended_guidance || prev.guidance
            }));
          }
        }
      }
    } catch (err) {
      console.error('Failed to analyze prompt:', err);
    } finally {
      setAnalyzingPrompt(false);
    }
  }, [selectedPreset, contentPresets]);

  // Analyze prompt when it changes (debounced)
  useEffect(() => {
    let promptForAnalysis = '';
    if (inputMode === 'single') {
      promptForAnalysis = (batchItems || '').trim();
    } else {
      promptForAnalysis = batchItems.split('\n').find(line => line.trim()) || '';
    }
    if (!promptForAnalysis || !autoEnhance) {
      setContentDetection(null);
      return;
    }

    const timeoutId = setTimeout(() => {
      analyzeCurrentPrompt(promptForAnalysis.trim());
    }, 500); // Debounce 500ms

    return () => clearTimeout(timeoutId);
  }, [batchItems, autoEnhance, analyzeCurrentPrompt, inputMode]);

  // NEW: Director expand (uses /batch-image/expand-concept; populates batchItems with plan shots for review/launch)
  const handleDirectorExpand = async () => {
    let idea = '';
    if (inputMode === 'single') {
      idea = (batchItems || lookAndFeel || '').trim();
    } else {
      idea = (batchItems || lookAndFeel || '').split('\n').find(l => l.trim()) || lookAndFeel;
    }
    if (!idea) {
      setError('Enter a high-level concept or look & feel first');
      return;
    }
    setIsExpanding(true);
    setError('');
    try {
      const resp = await fetch(`${API_BASE}/batch-image/expand-concept`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          idea,
          n: Math.max(1, Math.min(quantity || 4, 12)),
          look_and_feel: lookAndFeel,
          user_treatment: '',
          director_guidance: directorGuidance || undefined,
        })
      });
      if (!resp.ok) throw new Error('expand failed');
      const j = await resp.json();
      const plan = j?.data?.plan || j?.plan;
      const shots = (plan && plan.shots) || [];
      if (shots.length) {
        const lines = shots.map(s => (s.prompt || '').trim()).filter(Boolean);
        setBatchItems(lines.join('\n'));
        setInputMode('bulk'); // Director produces multiple -> switch to bulk
        setSuccess(`Director expanded to ${lines.length} coherent shots (review & launch)`);
      } else {
        setError('Director returned no shots');
      }
    } catch (e) {
      setError('Director expand failed (ollama may be busy)');
    } finally {
      setIsExpanding(false);
    }
  };

  const checkServiceStatus = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/status`);

      // Check if response is ok before parsing JSON
      if (!response.ok) {
        setError(`Service status check failed: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        setError('Service status response is not JSON');
        return;
      }

      const data = await response.json();

      if (!data.success) {
        setError('Batch image generation service is not available');
      }
    } catch (err) {
      setError('Failed to check service status');
    }
  }, []);

  // Load content presets from API
  const loadContentPresets = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/presets`);
      if (!response.ok) return;

      const data = await response.json();
      if (data.success && data.data.presets) {
        setContentPresets(data.data.presets);
      }
    } catch (err) {
      console.error('Failed to load content presets:', err);
    }
  }, []);

  // Handle preset selection
  const handlePresetChange = useCallback((presetName) => {
    setSelectedPreset(presetName);

    if (presetName !== 'auto' && contentPresets[presetName]) {
      const preset = contentPresets[presetName];
      setParams(prev => ({
        ...prev,
        steps: preset.recommended_steps || prev.steps,
        guidance: preset.recommended_guidance || prev.guidance,
        width: preset.recommended_dimensions?.[0] || prev.width,
        height: preset.recommended_dimensions?.[1] || prev.height
      }));
    }
  }, [contentPresets]);

  const fetchQueue = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/queue`);
      if (!response.ok) return;
      const data = await response.json();
      const rows = data?.data?.queue ?? data?.queue ?? [];
      setQueue(Array.isArray(rows) ? rows : []);
    } catch (err) {
      console.debug('Failed to load image batch queue:', err);
    }
  }, []);

  const loadBatchHistory = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/list`);

      if (!response.ok) {
        console.error(`Failed to load batch history: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        console.error('Response is not JSON:', contentType);
        return;
      }

      const data = await response.json();

      if (data.success) {
        let storedCleared = [];
        try {
          storedCleared = JSON.parse(localStorage.getItem('clearedImageBatchIds') || '[]');
        } catch (e) {
          storedCleared = [];
        }
        const rawBatches = data.data.batches || [];
        const filtered = rawBatches.filter((b) => !storedCleared.includes(b.batch_id));
        setBatchHistory(filtered);
      }
    } catch (err) {
      console.error('Failed to load batch history:', err);
    }
  }, []);

  const handleClearBatchList = useCallback(() => {
    const allIds = batchHistory.map((b) => b.batch_id);
    const updatedCleared = Array.from(new Set([...clearedBatchIds, ...allIds]));
    setClearedBatchIds(updatedCleared);
    try {
      localStorage.setItem('clearedImageBatchIds', JSON.stringify(updatedCleared));
    } catch (e) {
      console.error('Failed to save cleared batches to localStorage:', e);
    }
    setBatchHistory([]);
    setSuccess('Cleared batch history list.');
  }, [batchHistory, clearedBatchIds]);

  const hideBatch = useCallback((batchId) => {
    const updatedCleared = Array.from(new Set([...clearedBatchIds, batchId]));
    setClearedBatchIds(updatedCleared);
    try {
      localStorage.setItem('clearedImageBatchIds', JSON.stringify(updatedCleared));
    } catch (e) {
      console.error('Failed to save cleared batches to localStorage:', e);
    }
    setBatchHistory((prev) => prev.filter((b) => b.batch_id !== batchId));
  }, [clearedBatchIds]);

  const formatImageDate = useCallback((dStr) => {
    if (!dStr) return '';
    try {
      const d = new Date(dStr);
      if (isNaN(d.getTime())) return dStr;
      return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
      return dStr;
    }
  }, []);

  // Load service status and presets on mount
  useEffect(() => {
    checkServiceStatus();
    loadBatchHistory();
    loadContentPresets();
    fetchQueue();
  }, [fetchQueue, loadBatchHistory]);

  // Refresh queue panel while anything is active
  useEffect(() => {
    const active = queue.some((q) => POLLABLE_STATUSES.has(q.status));
    if (!active && !activeBatch) return undefined;
    const id = setInterval(() => { fetchQueue(); }, 2500);
    return () => clearInterval(id);
  }, [queue, activeBatch, fetchQueue]);

  const loadBatchById = useCallback(async (batchId) => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/status/${batchId}?include_results=true`);

      if (!response.ok) {
        setError(`Failed to load batch: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        setError('Batch response is not JSON');
        return;
      }

      const data = await response.json();

      if (data.success) {
        const batchStatus = data.data;
        setActiveBatch(batchStatus);

        const images = mapBatchResultsToImages(batchStatus);
        if (images.length > 0) {
          setGeneratedImages(images);
        }

        // Start polling if batch is still running (will be handled by the activeBatch useEffect)
        // No need to call startPolling here as the useEffect will handle it
      }
    } catch (err) {
      setError(`Failed to load batch: ${err.message}`);
    }
  }, []);

  const stopPolling = useCallback(() => {
    pollingBatchIdRef.current = null;
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  const startPolling = useCallback((batchId) => {
    stopPolling();
    pollingBatchIdRef.current = batchId;

    pollingRef.current = setInterval(async () => {
      try {
        const response = await fetch(`${API_BASE}/batch-image/status/${batchId}?include_results=true`);

        if (!response.ok) {
          console.error('Polling error: HTTP', response.status);
          if (response.status === 404) {
            stopPolling();
            setError('Batch not found');
          }
          return;
        }

        const data = await response.json();

        if (data.success) {
          const batchStatus = data.data;

          // Ignore stale responses after cancel/complete or batch switch
          if (pollingBatchIdRef.current !== batchId || batchStatus.batch_id !== batchId) {
            return;
          }

          setActiveBatch(batchStatus);

          const images = mapBatchResultsToImages(batchStatus);
          if (images.length > 0) {
            setGeneratedImages(images);
          }

          fetchQueue();

          // Stop polling if batch is complete
          if (['completed', 'error', 'cancelled'].includes(batchStatus.status)) {
            stopPolling();
            setSuccess(`Batch generation ${batchStatus.status}`);
            loadBatchHistory();
            fetchQueue();
          }
        }
      } catch (err) {
        console.error('Polling error:', err);
        // Don't stop polling on network errors, just log them
      }
    }, 2000);
  }, [stopPolling, fetchQueue, loadBatchHistory]);

  // Load specific batch if batch_id is in URL params (from ContentLibraryPage)
  useEffect(() => {
    const batchId = searchParams.get('batch');
    if (batchId) {
      loadBatchById(batchId);
    }
  }, [searchParams]);

  // Start/stop polling only when batch id or terminal status changes — not on every
  // progress tick (socket + poll both update activeBatch and used to restart polling,
  // clearing pollingRef and discarding in-flight status responses).
  const activeBatchId = activeBatch?.batch_id;
  const activeBatchStatus = activeBatch?.status;
  useEffect(() => {
    if (activeBatchId && POLLABLE_STATUSES.has(activeBatchStatus)) {
      startPolling(activeBatchId);
    } else {
      stopPolling();
    }

    return () => {
      stopPolling();
    };
  }, [activeBatchId, activeBatchStatus, startPolling, stopPolling]);

  // Keep generatedImages in sync with activeBatch.results (single source of truth for live batch).
  // This prevents stale/mismatched state during rapid polling updates.
  useEffect(() => {
    if (activeBatch && activeBatch.results && activeBatch.results.length > 0) {
      const synced = mapBatchResultsToImages(activeBatch);
      const syncedKey = synced.map((img) => img.id).join(',');
      const currentKey = generatedImages.map((img) => img.id).join(',');
      if (syncedKey !== currentKey) {
        setGeneratedImages(synced);
      }
    }
  }, [activeBatch]);

  // Monitor progress system for batch updates
  const completionHandledRef = useRef(null);
  useEffect(() => {
    const batchProcesses = Array.from(activeProcesses.values()).filter(
      process => (process.processType === 'image_generation' || process.process_type === 'image_generation') &&
        process.additional_data?.batch_id
    );

    if (batchProcesses.length > 0 && activeBatch) {
      const batchProcess = batchProcesses.find(
        p => p.additional_data.batch_id === activeBatch.batch_id
      );

      if (batchProcess) {
        // Update active batch with progress info from SocketIO
        setActiveBatch(prev => prev ? {
          ...prev,
          status: TERMINAL_BATCH_STATUSES.has(prev.status) ? prev.status : 'running',
          completed_images: batchProcess.additional_data.completed || prev.completed_images,
          progress_percentage: batchProcess.progress || prev.progress_percentage
        } : null);

        // On completion/error/cancel, do an immediate final poll to get all results
        if (['complete', 'end', 'error', 'cancelled'].includes(batchProcess.status) &&
            completionHandledRef.current !== activeBatch.batch_id) {
          completionHandledRef.current = activeBatch.batch_id;
          loadBatchById(activeBatch.batch_id);
          loadBatchHistory();
        }
      }
    }
  }, [activeProcesses, activeBatch?.batch_id, loadBatchById]);

  const handleBatchItemsChange = (event) => {
    setBatchItems(event.target.value);
  };

  const parseBatchItems = () => {
    // Split by lines and filter out empty lines
    const topics = batchItems
      .split('\n')
      .map(item => item.trim())
      .filter(item => item.length > 0);

    // If look & feel is provided, combine it with each topic
    if (lookAndFeel.trim()) {
      return topics.map(topic => `${topic}, ${lookAndFeel.trim()}`);
    }

    return topics;
  };

  // Handle quality preset changes
  const handleQualityPresetChange = (presetValue) => {
    const preset = qualityPresets.find(p => p.value === presetValue);
    if (preset) {
      setParams(prev => {
        const next = {
          ...prev,
          quality_preset: presetValue,
          steps: preset.steps,
          guidance: preset.guidance,
        };
        // Z-Image High 2K = official sampling at 2K (the real quality lever for
        // Turbo). Only the EXPLICIT 'high-2k' preset touches the canvas — the
        // old 'high' preset silently escalated to 2048² (2026-08-04 crashes).
        const modelKey = String(prev.model || 'auto').toLowerCase();
        const zimageFamily =
          modelKey.includes('zimage')
          || modelKey.includes('z-image')
          || modelKey === 'auto';
        if (zimageFamily && presetValue === 'high-2k') {
          next.width = 2048;
          next.height = 2048;
        }
        return next;
      });
    }
  };

  // Handle model changes
  const handleModelChange = (modelValue) => {
    setParams(prev => {
      let newParams = { ...prev, model: modelValue };

      // Adjust dimensions based on model capabilities.
      // Modern high-res models (SDXL, Z-Image, FLUX, Krea) and 'auto' → 1024.
      // SD1.5-class photoreal finetunes (realistic-vision, epic-realism) are 512-native.
      if (
        modelValue.includes('xl')
        || modelValue.startsWith('krea2')
        || modelValue === 'zimage-turbo'
        || modelValue.startsWith('flux')
        || modelValue === 'auto'
      ) {
        newParams.width = 1024;
        newParams.height = 1024;
      } else {
        newParams.width = 512;
        newParams.height = 512;
      }

      // Recommended defaults per model — quality sliders remain free to push higher.
      if (modelValue === 'krea2-raw') {
        newParams.steps = 52;
        newParams.guidance = 3.5;
      } else if (modelValue === 'krea2-turbo') {
        newParams.steps = 8;
        newParams.guidance = 0;
      } else if (modelValue === 'zimage-turbo') {
        newParams.steps = 9;
        newParams.guidance = 0;
      } else if (modelValue === 'flux-dev' || modelValue.startsWith('flux')) {
        // FLUX.1-dev max-quality defaults (FluxGuidance 3.5, 28 steps)
        newParams.steps = 28;
        newParams.guidance = 3.5;
        newParams.max_workers = 1; // VRAM safety — heavy Comfy graph
      }

      return newParams;
    });
  };

  const handleFileUpload = (event) => {
    const file = event.target.files[0];
    if (file && file.type === 'text/csv') {
      setCsvFile(file);
      setError('');
    } else {
      setError('Please select a valid CSV file');
      setCsvFile(null);
    }
  };

  const handleBlueprintFileUpload = (event) => {
    const file = event.target.files[0];
    const isCsv = file && (file.type === 'text/csv' || file.name.toLowerCase().endsWith('.csv'));
    if (isCsv) {
      setBlueprintFile(file);
      setError('');
    } else {
      setError('Please select a valid CSV file for blueprints');
      setBlueprintFile(null);
    }
  };

  const downloadTemplate = async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/template`);

      if (!response.ok) {
        setError(`Failed to download template: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (contentType && !contentType.includes('text/csv') && !contentType.includes('application/octet-stream')) {
        setError('Template response is not a valid CSV file');
        return;
      }

      const blob = await response.blob();

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'batch_generation_template.csv';
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err) {
      setError('Failed to download template: ' + err.message);
    }
  };

  const startGeneration = async () => {
    setLoading(true);
    setError('');
    setGeneratedImages([]);
    setSuccess('');

    try {
      let response;

      if (inputMode === 'blueprint') {
        if (!blueprintFile) {
          setError('Please select a CSV file for blueprint generation');
          setLoading(false);
          return;
        }

        const formData = new FormData();
        formData.append('file', blueprintFile);

        response = await fetch(`${API_BASE}/batch-image/generate/blueprints`, {
          method: 'POST',
          body: formData
        });
      } else if (inputMode === 'csv' && csvFile) {
        // CSV upload
        const formData = new FormData();
        formData.append('file', csvFile);

        // Add parameters
        Object.entries(params).forEach(([key, value]) => {
          formData.append(key, value.toString());
        });

        response = await fetch(`${API_BASE}/batch-image/generate/csv`, {
          method: 'POST',
          body: formData
        });
      } else if (inputMode === 'csv' && !csvFile) {
        setError('Please select a CSV file for upload');
        setLoading(false);
        return;
      } else if (inputMode === 'single') {
        // Single prompt: entire text (with newlines/paragraphs) as ONE prompt.
        // Quantity duplicates the exact same prompt text.
        const singlePrompt = (batchItems || '').trim();
        if (!singlePrompt) {
          setError('Please provide a prompt');
          setLoading(false);
          return;
        }
        let effectivePrompt = singlePrompt;
        if (lookAndFeel && lookAndFeel.trim()) {
          effectivePrompt = `${singlePrompt}, ${lookAndFeel.trim()}`;
        }
        let promptsToGenerate = [effectivePrompt];
        if (quantity > 1) {
          promptsToGenerate = Array(quantity).fill(effectivePrompt);
        }
        debugLog('Batch image single-prompt prepared', { promptCount: promptsToGenerate.length, quantity });

        const uiConfig = {
          inputMode,
          batchItems,
          lookAndFeel,
          quantity,
          params,
          castSubjectIds,
          selectedPreset,
          autoEnhance,
          enhanceAnatomy,
          enhanceFaces,
          enhanceHands,
          directorEnabled,
          directorGuidance,
        };

        response = await fetch(`${API_BASE}/batch-image/generate/prompts`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompts: promptsToGenerate,
            ...params,
            // Cast characters: backend resolves these to LoRA paths + trigger.
            subject_ids: castSubjectIds,
            // Quality enhancement parameters
            content_preset: selectedPreset === 'auto' ? null : selectedPreset,
            auto_enhance: autoEnhance,
            enhance_anatomy: enhanceAnatomy,
            enhance_faces: enhanceFaces,
            enhance_hands: enhanceHands,
            // Director (shared intelligent pipeline with MusicVideo / chat)
            director_mode: !!directorEnabled,
            director_guidance: directorGuidance || undefined,
            ui_config: uiConfig,
          })
        });
      } else {
        // Bulk input (one per line)
        const validPrompts = parseBatchItems();
        if (validPrompts.length === 0) {
          setError('Please provide at least one prompt or topic');
          setLoading(false);
          return;
        }

        // Duplicate prompts based on quantity (with numbering for bulk)
        let promptsToGenerate = validPrompts;
        if (quantity > 1) {
          promptsToGenerate = [];
          validPrompts.forEach(prompt => {
            for (let i = 0; i < quantity; i++) {
              promptsToGenerate.push(`${prompt} (${i + 1})`);
            }
          });
        }

        debugLog('Batch image prompts prepared', { promptCount: promptsToGenerate.length });

        const uiConfig = {
          inputMode,
          batchItems,
          lookAndFeel,
          quantity,
          params,
          castSubjectIds,
          selectedPreset,
          autoEnhance,
          enhanceAnatomy,
          enhanceFaces,
          enhanceHands,
          directorEnabled,
          directorGuidance,
        };

        response = await fetch(`${API_BASE}/batch-image/generate/prompts`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompts: promptsToGenerate,
            ...params,
            // Cast characters: backend resolves these to LoRA paths + trigger.
            subject_ids: castSubjectIds,
            // Quality enhancement parameters
            content_preset: selectedPreset === 'auto' ? null : selectedPreset,
            auto_enhance: autoEnhance,
            enhance_anatomy: enhanceAnatomy,
            enhance_faces: enhanceFaces,
            enhance_hands: enhanceHands,
            // Director (shared intelligent pipeline with MusicVideo / chat)
            director_mode: !!directorEnabled,
            director_guidance: directorGuidance || undefined,
            ui_config: uiConfig,
          })
        });
      }

      // Check if response is ok before parsing JSON
      if (!response.ok) {
        // Try to parse error response
        let errorMessage = `Failed to start generation: HTTP ${response.status}`;
        try {
          const errorData = await response.json();
          if (errorData.error) {
            if (typeof errorData.error === 'object' && errorData.error.message) {
              errorMessage = errorData.error.message;
            } else if (typeof errorData.error === 'string') {
              errorMessage = errorData.error;
            }
          } else if (errorData.message) {
            errorMessage = errorData.message;
          }
        } catch (e) {
          // If JSON parsing fails, use status text
          errorMessage = `Failed to start generation: HTTP ${response.status} ${response.statusText}`;
        }
        setError(errorMessage);
        setLoading(false);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        const text = await response.text();
        setError(`Generation response is not JSON. Response: ${text.substring(0, 200)}`);
        setLoading(false);
        return;
      }

      const data = await response.json();

      if (data.success) {
        const batchId = data.data?.batch_id;
        if (!batchId) {
          setError('Batch generation started but no batch ID returned');
          setLoading(false);
          return;
        }

        const initialStatus = data.data?.status || 'queued';
        setActiveBatch({
          batch_id: batchId,
          status: initialStatus,
          total_images: data.data?.prompt_count || data.data?.total_images || 0,
          completed_images: 0,
          failed_images: 0,
          progress_percentage: 0,
        });
        setSuccess(
          inputMode === 'blueprint'
            ? (data.data?.message || 'Blueprint batch queued. Results appear as generation completes.')
            : 'Batch queued. Worker drains one batch at a time — keep stacking them.',
        );
        // Free the form so the next job can be composed immediately (like Video Gen).
        if (inputMode === 'single' || inputMode === 'bulk') {
          setBatchItems('');
        }
        fetchQueue();
        loadBatchHistory();
      } else {
        const errorMsg = data.error?.message || data.error || data.message || 'Failed to start generation';
        setError(errorMsg);
      }
    } catch (err) {
      console.error('Generation error:', err);
      setError('Failed to start generation: ' + (err.message || String(err)));
    } finally {
      setLoading(false);
    }
  };

  const cancelGeneration = async () => {
    if (!activeBatch) return;

    try {
      const response = await fetch(`${API_BASE}/batch-image/cancel/${activeBatch.batch_id}`, {
        method: 'POST'
      });

      if (!response.ok) {
        setError(`Failed to cancel generation: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        setError('Cancel response is not JSON');
        return;
      }

      const data = await response.json();
      if (data.success) {
        setSuccess('Batch generation cancelled');
        stopPolling();
        setActiveBatch(prev => prev ? { ...prev, status: 'cancelled' } : null);
        fetchQueue();
      } else {
        setError(data.error || 'Failed to cancel generation');
      }
    } catch (err) {
      setError('Failed to cancel generation: ' + err.message);
    }
  };

  const handleAdjustRetry = async (batchId) => {
    try {
      const res = await fetch(`${API_BASE}/batch-image/status/${batchId}?include_results=true`);
      if (!res.ok) { setError(`Couldn't load settings: HTTP ${res.status}`); return; }
      const data = await res.json();
      const statusObj = data?.data || data;
      const rd = statusObj?.retry_data;
      const name = statusObj?.display_name || batchId.slice(0, 8);

      if (!rd) { setError("This batch didn't store its settings."); return; }

      const cfg = rd.params?.ui_config;
      if (cfg) {
        if (cfg.inputMode) setInputMode(cfg.inputMode);
        if (typeof cfg.batchItems === 'string') setBatchItems(cfg.batchItems);
        if (typeof cfg.lookAndFeel === 'string') setLookAndFeel(cfg.lookAndFeel);
        if (typeof cfg.quantity === 'number') setQuantity(cfg.quantity);
        if (cfg.params && typeof cfg.params === 'object') {
          setParams((prev) => ({ ...prev, ...cfg.params }));
        }
        if (Array.isArray(cfg.castSubjectIds)) setCastSubjectIds(cfg.castSubjectIds);
        if (cfg.selectedPreset) setSelectedPreset(cfg.selectedPreset);
        if (typeof cfg.autoEnhance === 'boolean') setAutoEnhance(cfg.autoEnhance);
        if (typeof cfg.enhanceAnatomy === 'boolean') setEnhanceAnatomy(cfg.enhanceAnatomy);
        if (typeof cfg.enhanceFaces === 'boolean') setEnhanceFaces(cfg.enhanceFaces);
        if (typeof cfg.enhanceHands === 'boolean') setEnhanceHands(cfg.enhanceHands);
        if (typeof cfg.directorEnabled === 'boolean') setDirectorEnabled(cfg.directorEnabled);
        if (typeof cfg.directorGuidance === 'string') setDirectorGuidance(cfg.directorGuidance);
        setSuccess(`Loaded "${name}" settings into control panel — adjust anything, then Start Generation.`);
      } else if (Array.isArray(rd.prompts)) {
        setBatchItems(rd.prompts.join('\n'));
        setInputMode('bulk');
        if (rd.params) {
          const p = rd.params;
          setParams((prev) => ({
            ...prev,
            model: p.model || prev.model,
            style: p.style || prev.style,
            width: p.width || prev.width,
            height: p.height || prev.height,
            steps: p.steps || prev.steps,
            guidance: p.guidance !== undefined ? p.guidance : prev.guidance,
          }));
        }
        setSuccess(`Loaded "${name}" prompts into panel.`);
      }
      setError('');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (e) {
      setError(`Couldn't load settings: ${e.message}`);
    }
  };

  const downloadResults = async (batchId) => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/download/${batchId}`);

      if (!response.ok) {
        setError(`Failed to download results: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (contentType && !contentType.includes('application/zip') && !contentType.includes('application/octet-stream')) {
        setError('Download response is not a valid ZIP file');
        return;
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `batch_${batchId}_results.zip`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err) {
      setError('Failed to download results: ' + err.message);
    }
  };

  const buildBatchImageUrl = useCallback((image, batchId) => {
    if (!batchId || !image?.imageFilename) return '';
    return `${API_BASE}/batch-image/image/${batchId}/${encodeFilename(image.imageFilename)}`;
  }, []);

  const openLightboxAt = useCallback((images, index) => {
    const img = images[index];
    if (!img) return;
    const batchId = img.batchId || activeBatch?.batch_id;
    const url = buildBatchImageUrl(img, batchId);
    if (!url) return;
    setLightboxImage({
      url,
      name: img.prompt || img.imageFilename || '',
      fileIndex: index,
    });
  }, [activeBatch?.batch_id, buildBatchImageUrl]);

  const openImageViewer = useCallback((image) => {
    const idx = generatedImages.findIndex(img => img.id === image.id);
    openLightboxAt(generatedImages, idx >= 0 ? idx : 0);
  }, [generatedImages, openLightboxAt]);

  const openHistoryBatchGallery = useCallback(async (batch, startIndex = 0) => {
    if (!batch?.batch_id) return;
    try {
      const response = await fetch(`${API_BASE}/batch-image/status/${batch.batch_id}?include_results=true`);
      if (!response.ok) {
        setError(`Failed to load batch: HTTP ${response.status}`);
        return;
      }
      const data = await response.json();
      if (!data.success) return;

      const batchStatus = data.data;
      const images = mapBatchResultsToImages(batchStatus);
      setActiveBatch(batchStatus);
      setGeneratedImages(images);

      const idx = Math.max(0, Math.min(startIndex, images.length - 1));
      if (images.length > 0) {
        openLightboxAt(images, idx);
      }
    } catch (err) {
      setError(`Failed to load batch: ${err.message}`);
    }
  }, [openLightboxAt]);

  const closeLightbox = useCallback(() => setLightboxImage(null), []);

  const handleLightboxPrev = useCallback(() => {
    if (!lightboxImage || lightboxImage.fileIndex <= 0) return;
    openLightboxAt(generatedImages, lightboxImage.fileIndex - 1);
  }, [lightboxImage, generatedImages, openLightboxAt]);

  const handleLightboxNext = useCallback(() => {
    if (!lightboxImage || lightboxImage.fileIndex >= generatedImages.length - 1) return;
    openLightboxAt(generatedImages, lightboxImage.fileIndex + 1);
  }, [lightboxImage, generatedImages, openLightboxAt]);

  const handleLightboxDownload = useCallback(() => {
    if (!lightboxImage) return;
    const img = generatedImages[lightboxImage.fileIndex];
    if (!img) return;
    const batchId = img.batchId || activeBatch?.batch_id;
    const link = document.createElement('a');
    link.href = buildBatchImageUrl(img, batchId);
    link.download = img.imageFilename || 'image.png';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, [lightboxImage, generatedImages, activeBatch?.batch_id, buildBatchImageUrl]);

  return (
    <PageLayout
      title={embedded ? undefined : "Image Generator"}
      variant={embedded ? "fullscreen" : "standard"}
      noPadding={embedded}
      actions={
        batchHistory.length > 0 ? (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={handleClearBatchList}
            sx={{ textTransform: 'none', borderRadius: 1 }}
          >
            Clear Batch List
          </Button>
        ) : null
      }
    >

      {/* Error/Success Messages */}
      {error && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => setError('')}>
          {error}
        </Alert>
      )}

      {success && (
        <Alert severity="success" sx={{ mb: 3 }} onClose={() => setSuccess('')}>
          {success}
        </Alert>
      )}

      <Grid container spacing={3}>
        {/* Input Section */}
        <Grid item xs={12} lg={6}>
          <Card sx={{
            height: 'fit-content',
            boxShadow: 2,
            borderRadius: 2
          }}>
            <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
              <Typography
                variant="h6"
                sx={{
                  fontWeight: 600,
                  mb: 3,
                  color: 'text.primary'
                }}
              >
                Generation Settings
              </Typography>

              {/* Content Preset Selector - Primary control for better images */}
              <Box sx={{ mb: 3, p: 2, bgcolor: 'primary.50', borderRadius: 2, border: '1px solid', borderColor: 'primary.200' }}>
                <Typography variant="subtitle2" sx={{ mb: 1.5, fontWeight: 600, color: 'primary.main' }}>
                  Content Type (for better quality)
                </Typography>
                <FormControl fullWidth size="small">
                  <Select
                    value={selectedPreset}
                    onChange={(e) => handlePresetChange(e.target.value)}
                    sx={{ bgcolor: 'background.paper' }}
                  >
                    <MenuItem value="auto">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Auto-detect (Recommended)</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Automatically optimizes settings based on your prompt
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="person_portrait">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Person - Portrait</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Headshots, face close-ups, profile photos
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="person_full_body">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Person - Full Body</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Standing, sitting, or posed full-body shots
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="person_working">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Person - Working/Action</Typography>
                        <Typography variant="caption" color="text.secondary">
                          People doing activities, using tools, interacting with objects
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="product_photo">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Product Photo</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Clean product shots, commercial photography
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="landscape">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Landscape/Scenery</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Nature, cityscapes, outdoor scenes
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="infographic_preset">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Infographic/Diagram</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Flat design, icons, vector graphics, charts
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="general">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>General Purpose</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Default settings for any content
                        </Typography>
                      </Box>
                    </MenuItem>
                  </Select>
                </FormControl>
              </Box>

              {/* Input Mode Selection */}
              <Box sx={{ mb: 3 }}>
                <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
                  Input Method
                </Typography>
                <Box sx={{ display: 'flex', gap: 1 }}>
                  <Button
                    variant={inputMode === 'single' ? 'contained' : 'outlined'}
                    onClick={() => setInputMode('single')}
                    size="small"
                    sx={{
                      flex: 1,
                      textTransform: 'none',
                      fontWeight: inputMode === 'single' ? 600 : 400
                    }}
                  >
                    Single Prompt
                  </Button>
                  <Button
                    variant={inputMode === 'bulk' ? 'contained' : 'outlined'}
                    onClick={() => setInputMode('bulk')}
                    size="small"
                    sx={{
                      flex: 1,
                      textTransform: 'none',
                      fontWeight: inputMode === 'bulk' ? 600 : 400
                    }}
                  >
                    Bulk Input
                  </Button>
                  <Button
                    variant={inputMode === 'csv' ? 'contained' : 'outlined'}
                    onClick={() => setInputMode('csv')}
                    size="small"
                    sx={{
                      flex: 1,
                      textTransform: 'none',
                      fontWeight: inputMode === 'csv' ? 600 : 400
                    }}
                  >
                    CSV Upload
                  </Button>
                  <Button
                    variant={inputMode === 'blueprint' ? 'contained' : 'outlined'}
                    onClick={() => setInputMode('blueprint')}
                    size="small"
                    sx={{
                      flex: 1,
                      textTransform: 'none',
                      fontWeight: inputMode === 'blueprint' ? 600 : 400
                    }}
                  >
                    Offline Blueprints
                  </Button>
                </Box>
              </Box>

              {/* Cast (optional) - for single prompt and bulk */}
              {(inputMode === 'single' || inputMode === 'bulk') && (
                <Box sx={{ mb: 2 }}>
                  <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
                    Cast (optional)
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                    Pick a trained character — identity (trigger + class + vision marks) and LoRA are applied automatically.
                    Write scene/action only; do not re-describe the face or costume.
                  </Typography>
                  <CharacterPicker
                    value={castSubjectIds}
                    onChange={setCastSubjectIds}
                    onlyTrained
                  />
                </Box>
              )}

              {/* Single Prompt Input (default) */}
              {inputMode === 'single' && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
                    Single Prompt
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                    Paste full prompt text (paragraphs and line breaks preserved as one prompt).
                    Use "Number of Images per Prompt" below for multiples from this exact prompt.
                  </Typography>
                  <TextField
                    fullWidth
                    multiline
                    rows={8}
                    placeholder="Paste your complete prompt here, including paragraphs if needed. The entire text will be used as a single prompt for image generation.&#10;&#10;Example: A detailed scene description with multiple sentences and line breaks all for one image concept..."
                    value={batchItems}
                    onChange={handleBatchItemsChange}
                    variant="outlined"
                    sx={{
                      mb: 2,
                      '& .MuiOutlinedInput-root': {
                        borderRadius: 1,
                        fontFamily: 'monospace',
                        fontSize: '0.9rem'
                      }
                    }}
                  />
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    Whole text = 1 prompt. Quantity duplicates it exactly for multiple images.
                  </Typography>
                </Box>
              )}

              {/* Bulk Input */}
              {inputMode === 'bulk' && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 500 }}>
                    Image Topics/Prompts
                  </Typography>
                  <TextField
                    fullWidth
                    multiline
                    rows={8}
                    placeholder="Enter image topics or prompts, one per line:&#10;&#10;A majestic mountain landscape at sunset&#10;A cat sitting on a windowsill&#10;Abstract geometric patterns in blue&#10;Portrait of a wise old wizard&#10;A futuristic city skyline&#10;..."
                    value={batchItems}
                    onChange={handleBatchItemsChange}
                    variant="outlined"
                    sx={{
                      mb: 2,
                      '& .MuiOutlinedInput-root': {
                        borderRadius: 1,
                        fontFamily: 'monospace',
                        fontSize: '0.9rem'
                      }
                    }}
                  />
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                    <Typography variant="caption" color="text.secondary">
                      {parseBatchItems().length} prompts ready for generation (bulk mode)
                    </Typography>
                    <Button
                      variant="text"
                      size="small"
                      onClick={() => {
                        setBatchItems(
                          'A majestic mountain landscape at sunset\n' +
                          'A cat sitting on a windowsill\n' +
                          'Abstract geometric patterns in blue\n' +
                          'Portrait of a wise old wizard\n' +
                          'A futuristic city skyline'
                        );
                        setLookAndFeel('photorealistic, professional photography, sharp focus, natural lighting');
                      }}
                      sx={{
                        textTransform: 'none',
                        fontSize: '0.75rem',
                        minWidth: 'auto',
                        px: 1,
                        py: 0.25
                      }}
                    >
                      Load Examples
                    </Button>
                  </Box>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    Tip: Each line becomes a separate image prompt
                  </Typography>
                </Box>
              )}

              {/* Look & Feel (shared for single + bulk) */}
              {(inputMode === 'single' || inputMode === 'bulk') && (
                <Box sx={{ mt: 3 }}>
                  <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
                    Look & Feel (Optional)
                  </Typography>
                  <TextField
                    fullWidth
                    multiline
                    rows={3}
                    placeholder="Describe the visual style to apply to the prompt(s):&#10;&#10;Examples:&#10;• In shades of blue, no text, darker colors&#10;• Minimalist black and white, clean lines&#10;• Professional infographic style, flat design&#10;• Photorealistic, dramatic lighting"
                    value={lookAndFeel}
                    onChange={(e) => setLookAndFeel(e.target.value)}
                    variant="outlined"
                    sx={{
                      mb: 1,
                      '& .MuiOutlinedInput-root': {
                        borderRadius: 1
                      }
                    }}
                  />
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Typography variant="caption" color="text.secondary">
                      {inputMode === 'single' ? 'Style will be appended to your single prompt' : `This style will be applied to all ${parseBatchItems().length} prompts above`}
                    </Typography>
                    <Button
                      variant="outlined"
                      size="small"
                      onClick={() => setShowPromptPreview(true)}
                      sx={{
                        textTransform: 'none',
                        fontSize: '0.75rem',
                        minWidth: 'auto',
                        px: 1.5,
                        py: 0.5
                      }}
                    >
                      Preview Prompts
                    </Button>
                  </Box>
                </Box>
              )}

              {/* CSV Upload */}
              {inputMode === 'csv' && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 500 }}>
                    CSV File Upload
                  </Typography>
                  <input
                    type="file"
                    accept=".csv"
                    onChange={handleFileUpload}
                    ref={fileInputRef}
                    style={{ display: 'none' }}
                  />

                  <Button
                    startIcon={<Upload />}
                    onClick={() => fileInputRef.current?.click()}
                    variant="outlined"
                    fullWidth
                    sx={{
                      mb: 2,
                      textTransform: 'none',
                      borderRadius: 1,
                      py: 1.5
                    }}
                  >
                    Upload CSV File
                  </Button>

                  {csvFile && (
                    <Alert severity="info" sx={{ mb: 2, borderRadius: 1 }}>
                      File selected: {csvFile.name}
                    </Alert>
                  )}

                  <Button
                    startIcon={<GetApp />}
                    onClick={downloadTemplate}
                    variant="text"
                    size="small"
                    sx={{
                      textTransform: 'none',
                      borderRadius: 1
                    }}
                  >
                    Download CSV Template
                  </Button>
                </Box>
              )}

              {/* Offline Blueprint Upload */}
              {inputMode === 'blueprint' && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 500 }}>
                    Offline Blueprint CSV Upload
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    Upload a CSV file with 'city' and 'count' (or 'patents') columns to generate data blueprints (CPU-only). Large batches run in the background; poll for status until complete.
                  </Typography>
                  <input
                    type="file"
                    accept=".csv"
                    onChange={handleBlueprintFileUpload}
                    ref={blueprintFileInputRef}
                    style={{ display: 'none' }}
                  />

                  <Button
                    startIcon={<Upload />}
                    onClick={() => blueprintFileInputRef.current?.click()}
                    variant="outlined"
                    fullWidth
                    sx={{
                      mb: 2,
                      textTransform: 'none',
                      borderRadius: 1,
                      py: 1.5
                    }}
                  >
                    Upload Blueprint CSV
                  </Button>

                  {blueprintFile && (
                    <Alert severity="info" sx={{ mb: 2, borderRadius: 1 }}>
                      File selected: {blueprintFile.name}
                    </Alert>
                  )}

                  <Typography variant="caption" color="text.secondary">
                    Accepted format: .csv (columns like city/name and count/patents/value)
                  </Typography>
                </Box>
              )}

              {inputMode !== 'blueprint' && (
                <>

                  {/* Current Settings Display */}
                  <Box sx={{ mt: 3, p: 2, backgroundColor: 'background.paper', borderRadius: 1, border: '1px solid', borderColor: 'divider' }}>
                    <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>Current Settings</Typography>
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                      <Chip
                        label={`Model: ${modelOptions.find(m => m.value === params.model)?.label || params.model}`}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={`Style: ${params.style}`}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={`Quality: ${params.quality_preset}`}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={`Size: ${params.width}x${params.height}`}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={`Steps: ${params.steps}`}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={`Workers: ${params.max_workers}`}
                        size="small"
                        variant="outlined"
                      />
                    </Box>
                  </Box>

                  {/* Generation Settings */}
                  <Accordion sx={{ mt: 3, borderRadius: 1 }}>
                    <AccordionSummary
                      expandIcon={<ExpandMore />}
                      sx={{
                        borderRadius: 1,
                        '&.Mui-expanded': {
                          borderRadius: '8px 8px 0 0'
                        }
                      }}
                    >
                      <Typography sx={{ fontWeight: 500 }}>Advanced Settings</Typography>
                    </AccordionSummary>
                    <AccordionDetails sx={{ pt: 2 }}>
                      <Grid container spacing={2}>
                        <Grid item xs={12} sm={6} md={4}>
                          <FormControl fullWidth>
                            <InputLabel>Model</InputLabel>
                            <Select
                              value={params.model}
                              onChange={(e) => handleModelChange(e.target.value)}
                            >
                              {modelOptions.map(option => (
                                <MenuItem key={option.value} value={option.value}>
                                  <Box>
                                    <Typography variant="body2">{option.label}</Typography>
                                    <Typography variant="caption" color="text.secondary">
                                      {option.description}
                                    </Typography>
                                  </Box>
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                        </Grid>

                        <Grid item xs={12} sm={6} md={4}>
                          <FormControl fullWidth>
                            <InputLabel>Quality Preset</InputLabel>
                            <Select
                              value={params.quality_preset}
                              onChange={(e) => handleQualityPresetChange(e.target.value)}
                            >
                              {qualityPresets
                                .filter((option) => {
                                  const isFlux = String(params.model || '').startsWith('flux');
                                  const isFluxPreset = option.value.startsWith('flux');
                                  return isFlux ? isFluxPreset || option.value === 'standard' : !isFluxPreset;
                                })
                                .map(option => (
                                <MenuItem key={option.value} value={option.value}>
                                  <Box>
                                    <Typography variant="body2">{option.label}</Typography>
                                    <Typography variant="caption" color="text.secondary">
                                      {option.description}
                                    </Typography>
                                  </Box>
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                        </Grid>

                        <Grid item xs={12} sm={6} md={4}>
                          <FormControl fullWidth>
                            <InputLabel>Style</InputLabel>
                            <Select
                              value={params.style}
                              onChange={(e) => setParams({ ...params, style: e.target.value })}
                            >
                              {styleOptions.map(option => (
                                <MenuItem key={option.value} value={option.value}>
                                  {option.label}
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                        </Grid>

                        <Grid item xs={12} sm={6} md={4}>
                          <TextField
                            fullWidth
                            label="Number of Images per Prompt"
                            type="number"
                            value={quantity}
                            onChange={(e) => setQuantity(Math.max(1, parseInt(e.target.value) || 1))}
                            inputProps={{ min: 1, max: 100 }}
                          />
                          <Button
                            variant="outlined"
                            size="small"
                            onClick={handleDirectorExpand}
                            disabled={isExpanding || !(batchItems || lookAndFeel).trim()}
                            title="Media Director (shared with MusicVideo): expand concept into N distinct coherent prompts"
                          >
                            {isExpanding ? '…' : 'Director expand'}
                          </Button>
                          <FormControlLabel
                            control={<Switch size="small" checked={directorEnabled} onChange={(e) => setDirectorEnabled(e.target.checked)} />}
                            label="use director"
                          />
                        </Grid>

                        <Grid item xs={12} sm={6} md={4}>
                          <FormControl fullWidth>
                            <InputLabel>Dimensions</InputLabel>
                            <Select
                              value={`${params.width}x${params.height}`}
                              onChange={(e) => {
                                const [width, height] = e.target.value.split('x').map(Number);
                                setParams({ ...params, width, height });
                              }}
                              renderValue={(v) => {
                                const match = dimensionPresets.find(
                                  (p) => `${p.width}x${p.height}` === v,
                                );
                                return match ? match.label : v;
                              }}
                            >
                              {!dimensionPresets.some(
                                (p) => p.width === params.width && p.height === params.height,
                              ) && (
                                <MenuItem value={`${params.width}x${params.height}`}>
                                  Custom ({params.width}x{params.height})
                                </MenuItem>
                              )}
                              {dimensionPresets.map(preset => (
                                <MenuItem key={`${preset.width}x${preset.height}`} value={`${preset.width}x${preset.height}`}>
                                  {preset.label}
                                </MenuItem>
                              ))}
                            </Select>
                            {(String(params.model).startsWith('flux')
                              || params.model === 'zimage-turbo'
                              || String(params.model).startsWith('krea2'))
                              && (params.width * params.height > 1024 * 1024) && (
                              <Typography variant="caption" color="warning.main" sx={{ mt: 0.5, display: 'block' }}>
                                High-res (&gt;1MP): more VRAM; may OOM on 16GB cards.
                              </Typography>
                            )}
                          </FormControl>
                        </Grid>

                        <Grid item xs={12}>
                          <Typography gutterBottom>
                            Steps: {params.steps}
                            {String(params.model).startsWith('flux') ? ' (FLUX quality ↑ with more steps)' : ''}
                          </Typography>
                          <Slider
                            value={params.steps}
                            onChange={(e, value) => setParams({ ...params, steps: value })}
                            min={1}
                            max={100}
                            step={1}
                            marks={[
                              { value: 8, label: '8' },
                              { value: 20, label: '20' },
                              { value: 28, label: '28' },
                              { value: 50, label: '50' },
                              { value: 100, label: '100' },
                            ]}
                          />
                        </Grid>

                        <Grid item xs={12}>
                          <Typography gutterBottom>
                            {String(params.model).startsWith('flux')
                              ? `FluxGuidance: ${params.guidance}`
                              : `Guidance Scale: ${params.guidance}`}
                          </Typography>
                          <Slider
                            value={params.guidance}
                            onChange={(e, value) => setParams({ ...params, guidance: value })}
                            min={0}
                            max={String(params.model).startsWith('flux') ? 6 : 20}
                            step={0.5}
                            marks
                          />
                        </Grid>

                        <Grid item xs={12}>
                          <Typography gutterBottom>
                            Max Workers: {params.max_workers}
                            {String(params.model).startsWith('flux') ? ' (FLUX forces 1 on server)' : ''}
                          </Typography>
                          <Slider
                            value={params.max_workers}
                            onChange={(e, value) => setParams({ ...params, max_workers: value })}
                            min={1}
                            max={4}
                            step={1}
                            marks
                            disabled={String(params.model).startsWith('flux')}
                          />
                        </Grid>

                        <Grid item xs={12}>
                          <FormControlLabel
                            control={
                              <Switch
                                checked={params.generate_thumbnails}
                                onChange={(e) => setParams({ ...params, generate_thumbnails: e.target.checked })}
                              />
                            }
                            label="Generate Thumbnails"
                          />
                        </Grid>
                      </Grid>
                    </AccordionDetails>
                  </Accordion>
                </>
              )}

              {/* Action Buttons — queue mode: never blocked by an in-flight batch */}
              <Box sx={{ mt: 3, display: 'flex', flexDirection: 'column', gap: 1 }}>
                <GpuGateBanner gpuBusy={gpuBusy} blockReason={blockReason} queueMode />
                <Box sx={{ display: 'flex', gap: 2 }}>
                  <Button
                    variant="contained"
                    onClick={startGeneration}
                    disabled={loading}
                    startIcon={<PlayArrow />}
                    fullWidth
                    size="large"
                    sx={{
                      textTransform: 'none',
                      borderRadius: 1,
                      py: 1.5,
                      fontWeight: 600
                    }}
                  >
                    {loading
                      ? 'Queuing…'
                      : (queue.some((q) => POLLABLE_STATUSES.has(q.status))
                        ? 'Add to Queue'
                        : 'Start Generation')}
                  </Button>

                  {activeBatch && POLLABLE_STATUSES.has(activeBatch.status) && (
                    <Button
                      variant="outlined"
                      onClick={cancelGeneration}
                      startIcon={<Cancel />}
                      color="error"
                      size="large"
                      sx={{
                        textTransform: 'none',
                        borderRadius: 1,
                        py: 1.5,
                        fontWeight: 600
                      }}
                    >
                      Cancel
                    </Button>
                  )}
                </Box>
              </Box>
            </CardContent>
          </Card>
        </Grid >

        {/* Progress and Results Section */}
        < Grid item xs={12} lg={6} >
          {/* Batch Queue panel — live view of stacked jobs (mirrors Video Gen) */}
          {queue.length > 0 && (
            <Card sx={{ mb: 3, boxShadow: 2, borderRadius: 2 }}>
              <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
                <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
                  <Typography variant="h6" sx={{ fontWeight: 600 }}>
                    Batch Queue
                  </Typography>
                  <Chip
                    label={`${queue.filter((q) => POLLABLE_STATUSES.has(q.status)).length} active`}
                    size="small"
                    color="primary"
                    variant="outlined"
                  />
                </Stack>
                <Stack spacing={1}>
                  {queue.map((q, idx) => {
                    const slotTag = `#${idx + 1}`;
                    const total = q.total_images || 0;
                    const done = (q.completed_images || 0) + (q.failed_images || 0);
                    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
                    const chipColor =
                      q.status === 'running' ? 'primary'
                        : q.status === 'queued' || q.status === 'pending' ? 'default'
                          : q.status === 'completed' ? 'success'
                            : q.status === 'cancelled' ? 'warning'
                              : q.status === 'error' ? 'error' : 'default';
                    const cancellable = POLLABLE_STATUSES.has(q.status);
                    return (
                      <Box
                        key={q.batch_id}
                        sx={{
                          p: 1.5,
                          border: '1px solid',
                          borderColor: q.is_running ? 'primary.main' : 'divider',
                          borderRadius: 1,
                          bgcolor: q.is_running ? 'action.hover' : 'transparent',
                        }}
                      >
                        <Stack direction="row" alignItems="center" spacing={1.5}>
                          <Chip
                            label={slotTag}
                            size="small"
                            variant="outlined"
                            sx={{ minWidth: 44, fontFamily: 'monospace' }}
                          />
                          <Box sx={{ flex: 1, minWidth: 0 }}>
                            <Typography variant="body2" noWrap title={q.display_name || q.batch_id}>
                              {q.display_name || q.batch_id}
                            </Typography>
                            <Typography variant="caption" color="text.secondary">
                              {done}/{total} images
                              {q.failed_images > 0 ? ` (${q.failed_images} failed)` : ''}
                            </Typography>
                          </Box>
                          <Chip label={(q.status || '').toUpperCase()} size="small" color={chipColor} />
                          {cancellable && (
                            <Tooltip
                              title={q.status === 'running' ? 'Cancel running batch' : 'Remove from queue'}
                              arrow
                            >
                              <IconButton
                                size="small"
                                onClick={async () => {
                                  try {
                                    await fetch(`${API_BASE}/batch-image/cancel/${q.batch_id}`, { method: 'POST' });
                                    fetchQueue();
                                    if (activeBatch?.batch_id === q.batch_id) {
                                      setActiveBatch((prev) => (prev ? { ...prev, status: 'cancelled' } : prev));
                                    }
                                  } catch (e) {
                                    setError(`Cancel failed: ${e.message}`);
                                  }
                                }}
                                aria-label="cancel batch"
                              >
                                <CloseIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          )}
                        </Stack>
                        {q.status === 'running' && (
                          <LinearProgress
                            variant="determinate"
                            value={pct}
                            sx={{ mt: 1, height: 4, borderRadius: 2 }}
                          />
                        )}
                      </Box>
                    );
                  })}
                </Stack>
              </CardContent>
            </Card>
          )}

          {/* Active Batch Progress */}
          {
            activeBatch && (
              <Card sx={{
                mb: 3,
                boxShadow: 2,
                borderRadius: 2
              }}>
                <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
                  <Typography
                    variant="h6"
                    sx={{
                      fontWeight: 600,
                      mb: 2,
                      color: 'text.primary'
                    }}
                  >
                    Current Progress
                  </Typography>

                  <Box sx={{ mb: 2 }}>
                    <Typography variant="body2" color="text.secondary">
                      Batch ID: {activeBatch.batch_id}
                    </Typography>
                    <Chip
                      label={activeBatch.status.toUpperCase()}
                      color={activeBatch.status === 'running' ? 'primary' :
                        activeBatch.status === 'completed' ? 'success' : 'default'}
                      size="small"
                      sx={{ mt: 1 }}
                    />
                  </Box>

                  <Box sx={{ mb: 2 }}>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
                      <Typography variant="body2">
                        Progress: {activeBatch.completed_images || 0}/{activeBatch.total_images || 0}
                      </Typography>
                      <Typography variant="body2">
                        {activeBatch.progress_percentage || 0}%
                      </Typography>
                    </Box>
                    <LinearProgress
                      variant="determinate"
                      value={activeBatch.progress_percentage || 0}
                    />
                  </Box>

                  <Box sx={{ display: 'flex', gap: 1.5, mt: 1, flexWrap: 'wrap' }}>
                    {activeBatch.status === 'completed' && (
                      <Button
                        startIcon={<Download />}
                        onClick={() => downloadResults(activeBatch.batch_id)}
                        variant="outlined"
                        size="small"
                      >
                        Download Results
                      </Button>
                    )}
                    {['completed', 'error', 'cancelled'].includes(activeBatch.status) && (
                      <Button
                        startIcon={<SettingsIcon />}
                        onClick={() => handleAdjustRetry(activeBatch.batch_id)}
                        variant="outlined"
                        size="small"
                      >
                        Adjust &amp; Retry
                      </Button>
                    )}
                  </Box>
                </CardContent>
              </Card>
            )
          }

          {/* Generated Images Gallery */}
          {
            (generatedImages.length > 0 || (activeBatch && activeBatch.results && activeBatch.results.some(r => r.success && r.image_path))) && (
              <Card sx={{
                boxShadow: 2,
                borderRadius: 2
              }}>
                <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
                  <Typography
                    variant="h6"
                    sx={{
                      fontWeight: 600,
                      mb: 2,
                      color: 'text.primary'
                    }}
                  >
                    Generated Images ({generatedImages.length || (activeBatch?.results?.filter(r => r.success && r.image_path).length || 0)})
                  </Typography>

                  <ImageList
                    cols={imageListCols}
                    gap={8}
                    sx={{
                      '& .MuiImageListItem-root': {
                        borderRadius: 1,
                        overflow: 'hidden'
                      }
                    }}
                  >
                    {generatedImages.map((image) => {
                      // Prefer embedded batchId (from the result that created this image) to avoid
                      // race conditions between setActiveBatch and setGeneratedImages during live polling.
                      // Falls back to activeBatch for older data.
                      const batchIdForUrl = image.batchId || (activeBatch && activeBatch.batch_id);
                      // Always try thumbnail first if we have a filename for it.
                      // Otherwise use the main image name (serving logic will fallback if needed).
                      let thumbnailUrl = '';
                      if (batchIdForUrl) {
                        if (image.thumbnailFilename) {
                          thumbnailUrl = `${API_BASE}/batch-image/image/${batchIdForUrl}/${image.thumbnailFilename}?thumbnail=true`;
                        } else if (image.imageFilename) {
                          thumbnailUrl = `${API_BASE}/batch-image/image/${batchIdForUrl}/${image.imageFilename}?thumbnail=true`;
                        }
                      }

                      return (
                        <ImageListItem key={image.id}>
                          <img
                            src={thumbnailUrl}
                            alt={image.prompt || 'Generated image'}
                            loading="lazy"
                            style={{ cursor: 'pointer' }}
                            onClick={() => openImageViewer(image)}
                            onError={(e) => {
                              console.error('Failed to load image thumbnail:', image.id, thumbnailUrl);
                              const batchIdForUrl = image.batchId || (activeBatch && activeBatch.batch_id);
                              // Robust fallback: always try the full original image (no ?thumbnail) before hiding.
                              // This ensures we show *something* even if dedicated thumbnail is missing or 404s.
                              if (image.imageFilename && batchIdForUrl && !e.target.dataset.fallbackAttempted) {
                                e.target.src = `${API_BASE}/batch-image/image/${batchIdForUrl}/${image.imageFilename}`;
                                e.target.dataset.fallbackAttempted = 'true';
                              } else if (!e.target.dataset.hidden) {
                                // Only hide as last resort
                                e.target.style.display = 'none';
                                e.target.dataset.hidden = 'true';
                              }
                            }}
                            role="button"
                            tabIndex={0}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault();
                                openImageViewer(image);
                              }
                            }}
                          />
                          <ImageListItemBar
                            title={image.prompt ? sanitizeText(image.prompt).substring(0, 30) + '...' : 'No prompt'}
                            actionIcon={
                              <IconButton
                                sx={{ color: 'rgba(255, 255, 255, 0.54)' }}
                                onClick={() => openImageViewer(image)}
                                aria-label={`View full image: ${sanitizeText(image.prompt) || 'Generated image'}`}
                              >
                                <Visibility />
                              </IconButton>
                            }
                          />
                        </ImageListItem>
                      );
                    })}
                  </ImageList>
                </CardContent>
              </Card>
            )
          }
          {/* Batch History — Stacked Thumbnail Gallery */}
          <Card sx={{
            mt: 3,
            boxShadow: 2,
            borderRadius: 2
          }}>
            <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
                <Typography variant="h6" sx={{ fontWeight: 600 }}>
                  Recent Batches
                </Typography>
                <Stack direction="row" spacing={1} alignItems="center">
                  {batchHistory.length > 0 && (
                    <Button
                      size="small"
                      variant="outlined"
                      color="inherit"
                      onClick={handleClearBatchList}
                      sx={{ textTransform: 'none', borderRadius: 1 }}
                    >
                      Clear Batch List
                    </Button>
                  )}
                  <IconButton size="small" onClick={loadBatchHistory} title="Refresh batches">
                    <RefreshIcon />
                  </IconButton>
                </Stack>
              </Stack>

              <Box sx={{ maxHeight: 520, overflowY: 'auto', pr: 0.5 }}>
                <Grid container spacing={2}>
                  {batchHistory.map((batch) => {
                    const dateStr = formatImageDate(batch.created_at || batch.start_time || batch.end_time);
                    const imgCount = batch.completed_images ?? batch.total_images ?? 0;
                    const rawName = batch.display_name || `Batch ${batch.batch_id.slice(0, 8)}`;
                    const label = rawName.length > 36 ? rawName.slice(0, 35).trimEnd() + '…' : rawName;
                    return (
                      <Grid item xs={12} sm={6} key={batch.batch_id}>
                        <Box
                          onClick={() => {
                            if (batch.status === 'completed') {
                              openHistoryBatchGallery(batch, 0);
                            } else {
                              loadBatchById(batch.batch_id);
                            }
                          }}
                          sx={{
                            cursor: 'pointer',
                            position: 'relative',
                            borderRadius: 2,
                            p: 1,
                            bgcolor: 'background.paper',
                            border: '1px solid',
                            borderColor: 'divider',
                            transition: 'transform 0.2s, box-shadow 0.2s',
                            '&:hover': {
                              transform: 'translateY(-2px)',
                              boxShadow: 4,
                              '& .batch-overlay': { opacity: 1 },
                              '& .batch-delete': { opacity: 1 },
                            },
                          }}
                        >
                          {/* Stacked thumbnail preview */}
                          <Box sx={{ position: 'relative', aspectRatio: '4/3', mb: 1 }}>
                            {imgCount > 2 && (
                              <Box sx={{
                                position: 'absolute', top: -6, left: 6, right: -6, bottom: 6,
                                bgcolor: 'grey.800', borderRadius: 1.5, border: 1, borderColor: 'grey.700',
                              }} />
                            )}
                            {imgCount > 1 && (
                              <Box sx={{
                                position: 'absolute', top: -3, left: 3, right: -3, bottom: 3,
                                bgcolor: 'grey.850', borderRadius: 1.5, border: 1, borderColor: 'grey.700',
                              }} />
                            )}
                            <Box sx={{
                              position: 'relative', width: '100%', height: '100%',
                              bgcolor: 'grey.900', borderRadius: 1.5, overflow: 'hidden',
                              border: 1, borderColor: 'grey.700',
                            }}>
                              {imgCount > 0 ? (
                                <Box
                                  component="img"
                                  src={`${API_BASE}/batch-image/preview/${batch.batch_id}`}
                                  alt="Preview"
                                  sx={{ width: '100%', height: '100%', objectFit: 'cover' }}
                                  onError={(e) => { e.target.style.display = 'none'; }}
                                />
                              ) : (
                                <Box sx={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                                  <ImageIcon sx={{ fontSize: 36, color: 'grey.600' }} />
                                </Box>
                              )}
                              <Box className="batch-overlay" sx={{
                                position: 'absolute', inset: 0,
                                bgcolor: 'rgba(0,0,0,0.4)',
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                opacity: 0, transition: 'opacity 0.2s',
                              }}>
                                <Visibility sx={{ fontSize: 32, color: 'white' }} />
                              </Box>
                              <Chip
                                label={`${imgCount} image${imgCount !== 1 ? 's' : ''}`}
                                size="small"
                                sx={{
                                  position: 'absolute', top: 6, right: 6,
                                  height: 20, fontSize: '0.65rem',
                                  bgcolor: 'rgba(0,0,0,0.7)', color: 'white',
                                  '& .MuiChip-label': { px: 0.75 },
                                }}
                              />
                              {batch.status !== 'completed' && (
                                <Chip
                                  label={batch.status}
                                  size="small"
                                  color={batch.status === 'error' ? 'error' : batch.status === 'cancelled' ? 'warning' : 'info'}
                                  sx={{
                                    position: 'absolute', bottom: 6, left: 6,
                                    height: 18, fontSize: '0.6rem',
                                  }}
                                />
                              )}
                              <Tooltip title="Clear batch from list">
                                <IconButton
                                  size="small"
                                  className="batch-delete"
                                  onClick={(e) => { e.stopPropagation(); hideBatch(batch.batch_id); }}
                                  sx={{
                                    position: 'absolute', top: 4, left: 4,
                                    width: 24, height: 24,
                                    bgcolor: 'rgba(0,0,0,0.6)', color: 'white',
                                    opacity: 0, transition: 'opacity 0.2s',
                                    '&:hover': { bgcolor: 'error.main' },
                                  }}
                                >
                                  <CloseIcon sx={{ fontSize: 16 }} />
                                </IconButton>
                              </Tooltip>
                            </Box>
                          </Box>

                          <Box sx={{ pt: 0.5 }}>
                            <Typography variant="subtitle2" noWrap title={rawName} sx={{ fontWeight: 600 }}>
                              {label}
                            </Typography>
                            {dateStr && (
                              <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.7rem', display: 'block', mb: 0.5 }}>
                                {dateStr}
                              </Typography>
                            )}

                            <Box sx={{ display: 'flex', gap: 0.5, mt: 1, flexWrap: 'wrap' }}>
                              {batch.status === 'completed' && (
                                <>
                                  <Button
                                    size="small"
                                    variant="outlined"
                                    startIcon={<Visibility sx={{ fontSize: 14 }} />}
                                    onClick={(e) => { e.stopPropagation(); openHistoryBatchGallery(batch, 0); }}
                                    sx={{ textTransform: 'none', borderRadius: 1, fontSize: '0.75rem', py: 0.2, px: 1 }}
                                  >
                                    Browse
                                  </Button>
                                  <Button
                                    size="small"
                                    variant="outlined"
                                    startIcon={<Download sx={{ fontSize: 14 }} />}
                                    onClick={(e) => { e.stopPropagation(); downloadResults(batch.batch_id); }}
                                    sx={{ textTransform: 'none', borderRadius: 1, fontSize: '0.75rem', py: 0.2, px: 1 }}
                                  >
                                    Download
                                  </Button>
                                </>
                              )}
                              <Button
                                size="small"
                                variant="outlined"
                                startIcon={<SettingsIcon sx={{ fontSize: 14 }} />}
                                onClick={(e) => { e.stopPropagation(); handleAdjustRetry(batch.batch_id); }}
                                sx={{ textTransform: 'none', borderRadius: 1, fontSize: '0.75rem', py: 0.2, px: 1 }}
                              >
                                Adjust &amp; Retry
                              </Button>
                            </Box>
                          </Box>
                        </Box>
                      </Grid>
                    );
                  })}
                </Grid>

                {batchHistory.length === 0 && (
                  <Box sx={{ textAlign: 'center', py: 4 }}>
                    <ImageIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
                    <Typography variant="body2" color="text.secondary">
                      No image batches found
                    </Typography>
                  </Box>
                )}
              </Box>
            </CardContent>
          </Card>
        </Grid >
      </Grid >

      {/* Full-screen batch image gallery (prev/next keyboard + arrows) */}
      {lightboxImage && (
        <ImageLightbox
          imageUrl={lightboxImage.url}
          imageName={lightboxImage.name}
          onClose={closeLightbox}
          onPrev={handleLightboxPrev}
          onNext={handleLightboxNext}
          onDownload={handleLightboxDownload}
          hasPrev={lightboxImage.fileIndex > 0}
          hasNext={lightboxImage.fileIndex < generatedImages.length - 1}
        />
      )}

      {/* Prompt Preview Dialog */}
      <Dialog
        open={showPromptPreview}
        onClose={() => setShowPromptPreview(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          Preview Generated Prompts ({inputMode === 'single' ? 1 : parseBatchItems().length}{inputMode === 'single' && quantity > 1 ? ` × ${quantity}` : ''})
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            These are the final prompts that will be sent to the image generator:
          </Typography>
          <Box sx={{ maxHeight: 400, overflow: 'auto' }}>
            {inputMode === 'single' ? (
              (() => {
                const single = (batchItems || '').trim();
                const items = quantity > 1 ? Array(quantity).fill(single) : [single];
                return items.map((prompt, index) => (
                  <Paper
                    key={index}
                    variant="outlined"
                    sx={{
                      p: 2,
                      mb: 1,
                      backgroundColor: 'background.default',
                      border: '1px solid',
                      borderColor: 'divider'
                    }}
                  >
                    <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                      <strong>{index + 1}.</strong> {sanitizeText(prompt)}
                    </Typography>
                  </Paper>
                ));
              })()
            ) : (
              parseBatchItems().map((prompt, index) => (
                <Paper
                  key={index}
                  variant="outlined"
                  sx={{
                    p: 2,
                    mb: 1,
                    backgroundColor: 'background.default',
                    border: '1px solid',
                    borderColor: 'divider'
                  }}
                >
                  <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                    <strong>{index + 1}.</strong> {sanitizeText(prompt)}
                  </Typography>
                </Paper>
              ))
            )}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setShowPromptPreview(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </PageLayout>
  );
};

export default BatchImageGeneratorPage;