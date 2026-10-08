// Reading files and folders dropped from the operating system.
//
// The browser empties DataTransfer.items as soon as the drop handler yields, so
// a handler that awaits between items only ever sees the first one. Take the
// snapshot synchronously inside the event, then walk it asynchronously.

/**
 * Capture every entry and file a drop carries, without awaiting.
 *
 * @param {DataTransfer} dataTransfer The drop event's dataTransfer.
 * @returns {{entries: FileSystemEntry[], looseFiles: File[], fallbackFiles: File[]}}
 *   `entries` for items the browser exposes as file-system entries (files and
 *   folders), `looseFiles` for file items without one, and `fallbackFiles`
 *   from `dataTransfer.files` for when neither yields anything.
 */
export function snapshotDrop(dataTransfer) {
  const entries = [];
  const looseFiles = [];
  const items = dataTransfer?.items;
  if (items) {
    for (let i = 0; i < items.length; i += 1) {
      const item = items[i];
      if (!item || item.kind !== 'file') continue;
      const entry = typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null;
      if (entry) {
        entries.push(entry);
      } else {
        const file = typeof item.getAsFile === 'function' ? item.getAsFile() : null;
        if (file) looseFiles.push(file);
      }
    }
  }
  const fallbackFiles = Array.from(dataTransfer?.files || []);
  return { entries, looseFiles, fallbackFiles };
}

async function readAllDirectoryEntries(dirEntry) {
  const reader = dirEntry.createReader();
  const all = [];
  // readEntries returns batches (about 100 in Chrome) until an empty one.
  for (;;) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch || batch.length === 0) return all;
    all.push(...batch);
  }
}

/**
 * Every file under a dropped entry, with its path relative to the drop.
 *
 * A dropped folder `Photos` holding `a.jpg` yields `Photos/a.jpg`; a dropped
 * file yields its bare name.
 *
 * @param {FileSystemEntry} entry
 * @param {string} [basePath]
 * @returns {Promise<Array<{file: File, relativePath: string}>>}
 */
export async function readEntryFiles(entry, basePath = '') {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    return [{ file, relativePath: basePath ? `${basePath}/${file.name}` : file.name }];
  }
  if (!entry.isDirectory) return [];
  const dirPath = basePath ? `${basePath}/${entry.name}` : entry.name;
  const out = [];
  for (const child of await readAllDirectoryEntries(entry)) {
    out.push(...(await readEntryFiles(child, dirPath)));
  }
  return out;
}

/**
 * Resolve a snapshot from {@link snapshotDrop} into files with relative paths.
 *
 * @param {ReturnType<typeof snapshotDrop>} snapshot
 * @returns {Promise<Array<{file: File, relativePath: string}>>}
 */
export async function collectDroppedFiles(snapshot) {
  const out = snapshot.looseFiles.map((file) => ({ file, relativePath: file.name }));
  for (const entry of snapshot.entries) {
    out.push(...(await readEntryFiles(entry)));
  }
  if (out.length === 0) {
    return snapshot.fallbackFiles.map((file) => ({ file, relativePath: file.name }));
  }
  return out;
}

/** True when a drag carries files from outside the page. */
export function dragCarriesFiles(event) {
  return Array.from(event?.dataTransfer?.types || []).includes('Files');
}
