// Layout rules for the sticky-notes board.

/**
 * Put pinned notes in a band across the top of the board, every other note below it.
 *
 * Pinned notes are packed left to right in reading order (top to bottom, then
 * left to right), wrapping at `cols`. Unpinned notes move down together, keeping
 * their arrangement, only as far as it takes to clear the band.
 *
 * @param {Array<{i: string, x: number, y: number, w: number, h: number}>} layout
 * @param {Record<string, boolean>} pinned Note id → pinned.
 * @param {number} cols Grid columns.
 * @param {(item: object) => number} [rowsOf] Rows a note occupies; a minimized
 *   note is shorter than its stored height.
 * @returns {Array<object>} A new layout in the input's order.
 */
export function pinnedToTop(layout, pinned, cols, rowsOf = (item) => item.h) {
  const pinnedItems = layout.filter((item) => pinned[item.i]);
  if (pinnedItems.length === 0) return layout;

  const placed = new Map();
  let x = 0;
  let y = 0;
  let rowH = 0;
  const readingOrder = [...pinnedItems].sort((a, b) => a.y - b.y || a.x - b.x);
  for (const item of readingOrder) {
    const w = Math.min(item.w, cols);
    if (x > 0 && x + w > cols) {
      y += rowH;
      x = 0;
      rowH = 0;
    }
    placed.set(item.i, { x, y });
    x += w;
    rowH = Math.max(rowH, rowsOf(item));
  }
  const bandH = y + rowH;

  const unpinned = layout.filter((item) => !pinned[item.i]);
  const topOfRest = unpinned.length ? Math.min(...unpinned.map((item) => item.y)) : bandH;
  const shift = Math.max(0, bandH - topOfRest);

  return layout.map((item) => {
    const spot = placed.get(item.i);
    if (spot) return { ...item, ...spot };
    return shift ? { ...item, y: item.y + shift } : item;
  });
}

/** Note ids with the pinned ones first; each group keeps its given order. */
export function pinnedFirst(ids, pinned) {
  return [...ids.filter((id) => pinned[id]), ...ids.filter((id) => !pinned[id])];
}

/**
 * True when a key event comes from somewhere that edits text and has its own
 * undo: an input, a textarea, or a contenteditable region such as a note body.
 */
export function isEditableTarget(target) {
  if (!target || typeof target !== 'object') return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return Boolean(target.closest?.('[contenteditable]:not([contenteditable="false"])'));
}
