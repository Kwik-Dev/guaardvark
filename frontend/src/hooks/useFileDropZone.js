// frontend/src/hooks/useFileDropZone.js
// Turns an element into a drop target for local files.
//
//   const drop = useFileDropZone({ onFiles: (files) => attach(files) });
//   <Box {...drop.dropProps}>{drop.isDragActive && <FileDropOverlay />}</Box>
//
// Only drags that carry files are taken; text dragged inside the page keeps the
// browser's behaviour. A taken drop is cancelled and stopped here, so it never
// reaches the browser (which would open the file) or an outer drop target.
// Only dataTransfer.files is read: a drag from a browser tab also carries the
// remote URL, and fetching it would break offline-first.

import { useCallback, useRef, useState } from "react";

const carriesFiles = (event) => {
  const types = event.dataTransfer?.types;
  if (!types) return false;
  return Array.from(types).includes("Files");
};

/**
 * @param {object} opts
 * @param {function(File[]): void} opts.onFiles called with the dropped files
 * @param {boolean} [opts.enabled=true] when false, drops are swallowed and ignored
 * @returns {{dropProps: object, isDragActive: boolean}}
 */
export default function useFileDropZone({ onFiles, enabled = true }) {
  const [isDragActive, setIsDragActive] = useState(false);
  // dragenter/dragleave fire for every child crossed; count to know when the
  // pointer has left the zone itself.
  const depthRef = useRef(0);
  const onFilesRef = useRef(onFiles);
  onFilesRef.current = onFiles;

  const onDragEnter = useCallback((event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    depthRef.current += 1;
    setIsDragActive(true);
  }, []);

  const onDragOver = useCallback(
    (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.dataTransfer) event.dataTransfer.dropEffect = enabled ? "copy" : "none";
    },
    [enabled],
  );

  const onDragLeave = useCallback((event) => {
    if (!carriesFiles(event)) return;
    event.stopPropagation();
    depthRef.current = Math.max(0, depthRef.current - 1);
    if (depthRef.current === 0) setIsDragActive(false);
  }, []);

  const onDrop = useCallback(
    (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      event.stopPropagation();
      depthRef.current = 0;
      setIsDragActive(false);
      const files = Array.from(event.dataTransfer?.files || []);
      if (enabled && files.length) onFilesRef.current?.(files);
    },
    [enabled],
  );

  return {
    dropProps: { onDragEnter, onDragOver, onDragLeave, onDrop },
    isDragActive: enabled && isDragActive,
  };
}
