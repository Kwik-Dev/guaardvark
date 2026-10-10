// Helpers for the image editor's operation list, which the backend applies
// in order (/api/files/image/edit).

// The part of the original image the applied crops leave, in natural pixels,
// or null when nothing is cropped. Each crop op is relative to the image as
// the previous ops left it, so a second crop is offset by the first.
export function cropRectFromOperations(operations) {
  let rect = null;
  for (const op of operations || []) {
    if (op.type !== 'crop') continue;
    rect = {
      x: (rect ? rect.x : 0) + op.x,
      y: (rect ? rect.y : 0) + op.y,
      width: op.width,
      height: op.height,
    };
  }
  return rect;
}

// Display scale that fits a width x height region of natural pixels in the
// editor canvas without enlarging it: the same limits the uncropped preview
// gets from CSS (90vw wide, the viewport minus the 52px toolbar high).
export function fitScale(width, height, viewportWidth, viewportHeight) {
  if (!width || !height) return 1;
  return Math.min(1, (viewportWidth * 0.9) / width, (viewportHeight - 52) / height);
}
