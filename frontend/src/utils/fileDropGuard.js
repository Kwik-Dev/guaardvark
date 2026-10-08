// Window-level guard for files dropped outside any drop zone.
//
// A file dropped where nothing handles it makes the browser open or download
// it, which navigates away from the app. Drop zones cancel their own dragover
// and drop, so the guard listens in the bubble phase and only acts on events
// nobody has cancelled.

import { dragCarriesFiles } from './droppedFiles';

const isNativeFileInput = (target) =>
  Boolean(target && target.tagName === 'INPUT' && target.type === 'file');

/**
 * Stop unhandled file drops from navigating away.
 *
 * @param {EventTarget} [target=window]
 * @returns {() => void} Removes the listeners.
 */
export function installFileDropGuard(target = window) {
  const onDragOver = (event) => {
    if (event.defaultPrevented || !dragCarriesFiles(event) || isNativeFileInput(event.target)) return;
    event.preventDefault();
    // Shows the "no drop" cursor over places that do not take files.
    event.dataTransfer.dropEffect = 'none';
  };
  const onDrop = (event) => {
    if (event.defaultPrevented || !dragCarriesFiles(event) || isNativeFileInput(event.target)) return;
    event.preventDefault();
  };
  target.addEventListener('dragover', onDragOver);
  target.addEventListener('drop', onDrop);
  return () => {
    target.removeEventListener('dragover', onDragOver);
    target.removeEventListener('drop', onDrop);
  };
}
