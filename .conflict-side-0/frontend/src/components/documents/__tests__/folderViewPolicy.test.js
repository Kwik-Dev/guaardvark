import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  coerceListingView,
  folderHasVisualMedia,
  persistListingView,
  readStoredListingView,
  VIEW_STORAGE_KEY,
} from '../folderViewPolicy';

describe('folderViewPolicy', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('coerces gallery and unknown views back to list', () => {
    expect(coerceListingView('media')).toBe('list');
    expect(coerceListingView('unknown')).toBe('list');
    expect(coerceListingView(null)).toBe('list');
  });

  it('keeps list and grid as listing views', () => {
    expect(coerceListingView('grid')).toBe('grid');
    expect(coerceListingView('list')).toBe('list');
  });

  it('treats mixed image + document folders as having visual media', () => {
    expect(folderHasVisualMedia([
      { filename: 'a.png' },
      { filename: 'notes.pdf' },
    ])).toBe(true);
  });

  it('does not treat audio-only folders as visual media', () => {
    expect(folderHasVisualMedia([{ filename: 'a.wav' }])).toBe(false);
    expect(folderHasVisualMedia([{ filename: 'notes.pdf' }])).toBe(false);
    expect(folderHasVisualMedia([])).toBe(false);
  });

  it('reads a stored listing view and ignores a stored gallery default', () => {
    localStorage.setItem(VIEW_STORAGE_KEY, 'grid');
    expect(readStoredListingView()).toBe('grid');
    localStorage.setItem(VIEW_STORAGE_KEY, 'media');
    expect(readStoredListingView()).toBe('list');
  });

  it('never persists gallery as the listing default', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    expect(persistListingView('media')).toBe('list');
    expect(localStorage.getItem(VIEW_STORAGE_KEY)).toBe('list');
    expect(persistListingView('grid')).toBe('grid');
    expect(localStorage.getItem(VIEW_STORAGE_KEY)).toBe('grid');
    const written = setItem.mock.calls
      .filter(([key]) => key === VIEW_STORAGE_KEY)
      .map(([, value]) => value);
    expect(written).not.toContain('media');
    setItem.mockRestore();
  });
});
