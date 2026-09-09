import { describe, expect, it } from 'vitest';
import {
  buildFinalPrompts,
  clampQuantity,
  isZimageModel,
  modelFamily,
  qualityPresetsForModel,
  resolveQualityPreset,
} from './batchImageSettings';

describe('modelFamily', () => {
  it('classifies the catalog ids', () => {
    expect(modelFamily('auto')).toBe('auto');
    expect(modelFamily('zimage-turbo')).toBe('zimage');
    expect(modelFamily('flux-dev')).toBe('flux');
    expect(modelFamily('krea2-turbo')).toBe('krea-turbo');
    expect(modelFamily('krea2-raw')).toBe('krea-raw');
    expect(modelFamily('sd-xl')).toBe('sdxl');
    expect(modelFamily('sdxl-turbo')).toBe('sdxl-turbo');
    expect(modelFamily('realistic-vision')).toBe('sd');
  });

  it('does not treat auto as Z-Image (the router may pick SDXL)', () => {
    expect(isZimageModel('auto')).toBe(false);
    expect(isZimageModel('zimage-turbo')).toBe(true);
  });
});

describe('quality presets', () => {
  it('gives sdxl-turbo its own 1-4 step CFG-free presets', () => {
    const presets = qualityPresetsForModel('sdxl-turbo');
    expect(presets.every((p) => p.steps <= 4 && p.guidance === 0)).toBe(true);
    // SDXL Base presets would run at numbers the validator clamps away.
    expect(qualityPresetsForModel('sd-xl').some((p) => p.steps === 25)).toBe(true);
  });

  it('keeps the current preset when the new family offers it', () => {
    expect(resolveQualityPreset('sd-xl', 'fast')).toBe('fast');
    expect(resolveQualityPreset('zimage-turbo', 'standard')).toBe('standard');
  });

  it('falls back to the family default for an out-of-range preset', () => {
    expect(resolveQualityPreset('flux-dev', 'standard')).toBe('flux-quality');
    expect(resolveQualityPreset('krea2-raw', 'fast')).toBe('high');
    expect(resolveQualityPreset('zimage-turbo', 'flux-ultra')).toBe('standard');
    expect(resolveQualityPreset('sd-xl', undefined)).toBe('standard');
  });

  it('every family default exists in its own list', () => {
    for (const model of ['auto', 'zimage-turbo', 'flux-dev', 'krea2-turbo', 'krea2-raw', 'sd-xl', 'sdxl-turbo', 'realistic-vision']) {
      const def = resolveQualityPreset(model, 'not-a-preset');
      expect(qualityPresetsForModel(model).map((p) => p.value)).toContain(def);
    }
  });
});

describe('clampQuantity', () => {
  it('clamps to 1..100', () => {
    expect(clampQuantity('0')).toBe(1);
    expect(clampQuantity('')).toBe(1);
    expect(clampQuantity('7')).toBe(7);
    expect(clampQuantity('250')).toBe(100);
  });
});

describe('buildFinalPrompts', () => {
  it('single mode: whole text is one prompt with Look & Feel appended', () => {
    expect(
      buildFinalPrompts({ inputMode: 'single', batchItems: 'a fox\nin snow', lookAndFeel: ' film grain ', quantity: 1 }),
    ).toEqual(['a fox\nin snow, film grain']);
  });

  it('bulk mode: one per non-empty line, repeated by quantity, no copy counter', () => {
    expect(
      buildFinalPrompts({ inputMode: 'bulk', batchItems: 'a\n\n b \n', lookAndFeel: '', quantity: 2 }),
    ).toEqual(['a', 'a', 'b', 'b']);
  });

  it('returns [] for empty input so callers can refuse to start', () => {
    expect(buildFinalPrompts({ inputMode: 'single', batchItems: '  ', lookAndFeel: 'x', quantity: 3 })).toEqual([]);
    expect(buildFinalPrompts({ inputMode: 'bulk', batchItems: '\n\n', lookAndFeel: '', quantity: 1 })).toEqual([]);
  });
});
