// Pure helpers for the Batch Image Generator page: model-family detection,
// per-family quality presets, and the prompt list that is actually submitted.
//
// Kept out of the page component so the Preview dialog and startGeneration
// share one prompt builder (they used to disagree about Look & Feel) and so
// the family logic has a single definition instead of four inline tests that
// disagreed about `auto` and exact-vs-fuzzy matching.

/**
 * Sampling family for a catalog model id. `auto` is its own family: the
 * backend router may pick Z-Image, SDXL (for text-bearing prompts) or another
 * downloaded model, so the UI must not lock the guidance slider for it.
 */
export function modelFamily(model) {
  const m = String(model || 'auto').toLowerCase();
  if (m === 'auto') return 'auto';
  if (m.includes('flux')) return 'flux';
  if (m.includes('zimage') || m.includes('z-image')) return 'zimage';
  if (m.includes('krea')) return m.includes('raw') ? 'krea-raw' : 'krea-turbo';
  if (m.includes('xl')) return m.includes('turbo') ? 'sdxl-turbo' : 'sdxl';
  return 'sd';
}

/** Z-Image proper (CFG-distilled). `auto` is deliberately NOT included. */
export const isZimageModel = (model) => modelFamily(model) === 'zimage';

export const ZIMAGE_STEPS = 9;
export const ZIMAGE_GUIDANCE = 0;

// Steps each Z-Image preset legitimately uses, so the guard in the page corrects
// foreign values without fighting the model's own Fast / Standard / High-2K presets.
export const ZIMAGE_PRESET_STEPS = { fast: 6, standard: 9, 'high-2k': 9 };

const ZIMAGE_PRESETS = [
  { value: 'fast', label: 'Fast', steps: 6, guidance: 0.0, description: 'Quick draft' },
  { value: 'standard', label: 'Standard', steps: 9, guidance: 0.0, description: 'Official Turbo recipe (HF)' },
  // The 2K jump is its own explicitly-labelled preset: a preset that silently
  // rewrote the canvas to 2048x2048 put 16GB cards into memory crashes.
  { value: 'high-2k', label: 'High 2K (2048²)', steps: 9, guidance: 0.0, description: 'Official recipe at 2K canvas — heavy; 16GB cards may refuse' },
];

const PRESETS_BY_FAMILY = {
  flux: [
    { value: 'flux-fast', label: 'FLUX Fast', steps: 16, guidance: 3.0, description: 'Faster FLUX.1-dev stills' },
    { value: 'flux-quality', label: 'FLUX Max Quality', steps: 28, guidance: 3.5, description: 'Default max-quality FLUX.1-dev' },
    { value: 'flux-ultra', label: 'FLUX Ultra', steps: 40, guidance: 4.0, description: 'Highest steps — slow, peak detail' },
  ],
  zimage: ZIMAGE_PRESETS,
  // `auto` resolves to the product default (Z-Image) when nothing else fits,
  // so its presets mirror that family; guidance stays editable.
  auto: ZIMAGE_PRESETS,
  'krea-turbo': [
    { value: 'fast', label: 'Fast', steps: 6, guidance: 0.0, description: 'Krea turbo CFG-free' },
    { value: 'standard', label: 'Standard', steps: 8, guidance: 0.0, description: 'Balanced turbo' },
    { value: 'high', label: 'High Quality', steps: 12, guidance: 0.0, description: 'More steps' },
  ],
  'krea-raw': [
    { value: 'standard', label: 'Standard', steps: 40, guidance: 3.5, description: 'Krea raw quality' },
    { value: 'high', label: 'High Quality', steps: 52, guidance: 3.5, description: 'Default raw' },
    { value: 'ultra', label: 'Ultra', steps: 60, guidance: 3.5, description: 'Slow, peak detail' },
  ],
  // SDXL Turbo is a 1-4 step, CFG-free distillation; the backend validator
  // clamps to that range (settings_validator.py), so offering the SDXL Base
  // 20-35 step presets showed numbers that never ran.
  'sdxl-turbo': [
    { value: 'fast', label: 'Fast', steps: 1, guidance: 0.0, description: 'Single-step preview' },
    { value: 'standard', label: 'Standard', steps: 4, guidance: 0.0, description: 'Turbo recipe (4 steps, CFG-free)' },
  ],
  sdxl: [
    { value: 'fast', label: 'Fast', steps: 20, guidance: 6.0, description: 'Quick SDXL' },
    { value: 'standard', label: 'Standard', steps: 25, guidance: 7.0, description: 'Balanced SDXL' },
    { value: 'high', label: 'High Quality', steps: 35, guidance: 7.5, description: 'Final SDXL' },
  ],
  sd: [
    { value: 'fast', label: 'Fast', steps: 15, guidance: 7.0, description: 'Quick generation, good for testing' },
    { value: 'standard', label: 'Standard', steps: 20, guidance: 7.5, description: 'Balanced quality and speed' },
    { value: 'high', label: 'High Quality', steps: 30, guidance: 8.0, description: 'High quality, slower generation' },
  ],
};

/** Quality presets offered for a model. */
export function qualityPresetsForModel(model) {
  return PRESETS_BY_FAMILY[modelFamily(model)] || PRESETS_BY_FAMILY.sd;
}

// The preset whose steps/guidance match the family's recommended recipe.
const DEFAULT_PRESET_BY_FAMILY = {
  flux: 'flux-quality',
  zimage: 'standard',
  auto: 'standard',
  'krea-turbo': 'standard',
  'krea-raw': 'high',
  'sdxl-turbo': 'standard',
  sdxl: 'standard',
  sd: 'standard',
};

/**
 * Preset to select after switching to `model`. Keeps `current` when the new
 * family offers it; otherwise the family default. Without this a value from
 * another family (e.g. 'flux-quality' after leaving FLUX) sat on the Select as
 * an out-of-range value and the "Quality" chip described settings not in use.
 */
export function resolveQualityPreset(model, current) {
  const presets = qualityPresetsForModel(model);
  if (current && presets.some((p) => p.value === current)) return current;
  return DEFAULT_PRESET_BY_FAMILY[modelFamily(model)] || 'standard';
}

// Legacy batches stored prompts with the old " (i+1)" copy counter baked in.
// Strip it when rehydrating so re-runs don't compound the marker.
// Mirrors _TRAILING_COUNTER in backend/services/image_prompt_sanitize.py.
export const stripCopyCounter = (p) =>
  String(p ?? '').replace(/(?:\s*\(\d{1,3}\))+\s*$/, '').trim() || String(p ?? '');

/** Highest quantity the page accepts; the backend caps the prompt list to match. */
export const MAX_QUANTITY = 100;

export function clampQuantity(value) {
  const n = parseInt(value, 10);
  if (!Number.isFinite(n) || n < 1) return 1;
  return Math.min(n, MAX_QUANTITY);
}

/**
 * The exact prompt list that will be submitted for single/bulk mode.
 *
 * - single: the whole textarea is ONE prompt (line breaks preserved).
 * - bulk: one prompt per non-empty line.
 * Look & Feel, when set, is appended to every prompt as ", <look>". Quantity
 * repeats each prompt verbatim; the backend keys images by position, so no
 * copy counter is added to the text.
 */
export function buildFinalPrompts({ inputMode, batchItems, lookAndFeel, quantity }) {
  const look = String(lookAndFeel || '').trim();
  const withLook = (p) => (look ? `${p}, ${look}` : p);

  let base;
  if (inputMode === 'single') {
    const single = String(batchItems || '').trim();
    base = single ? [single] : [];
  } else {
    base = String(batchItems || '')
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);
  }

  const copies = clampQuantity(quantity);
  const out = [];
  base.forEach((p) => {
    const finalPrompt = withLook(p);
    for (let i = 0; i < copies; i++) out.push(finalPrompt);
  });
  return out;
}
