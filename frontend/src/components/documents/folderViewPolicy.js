// Listing vs gallery rules for Files page folder windows.
// Views change presentation, never folder membership. Gallery is opt-in.

import { isImageFile, isVideoFile } from './fileUtils.jsx';

export const LISTING_VIEWS = ['list', 'grid'];
export const DEFAULT_LISTING_VIEW = 'list';
export const VIEW_STORAGE_KEY = 'documentsPageViewMode';

export function isVisualMediaFile(filename) {
  return isImageFile(filename) || isVideoFile(filename);
}

export function folderHasVisualMedia(files) {
  return (files || []).some((f) => isVisualMediaFile(f.filename || f.name));
}

export function coerceListingView(viewMode) {
  return LISTING_VIEWS.includes(viewMode) ? viewMode : DEFAULT_LISTING_VIEW;
}

export function readStoredListingView() {
  try {
    return coerceListingView(localStorage.getItem(VIEW_STORAGE_KEY));
  } catch {
    return DEFAULT_LISTING_VIEW;
  }
}

export function persistListingView(viewMode) {
  const listing = coerceListingView(viewMode);
  try {
    localStorage.setItem(VIEW_STORAGE_KEY, listing);
  } catch {
    // localStorage can throw in private mode
  }
  return listing;
}
