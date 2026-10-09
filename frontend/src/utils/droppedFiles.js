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

/**
 * The name the server stores a new folder under.
 *
 * Mirrors werkzeug's `secure_filename`, which the folder route applies: accents
 * folded to ASCII, runs of whitespace joined with `_`, anything outside
 * `A-Za-z0-9_.-` dropped, and leading or trailing `.`/`_` trimmed. "Weekend in
 * Lisbon" is stored as "Weekend_in_Lisbon".
 *
 * @param {string} name
 * @returns {string}
 */
export function storedFolderName(name) {
  return String(name ?? '')
    .normalize('NFKD')
    .replace(/[^ -~\t\n\r\v\f]/g, '')
    .replace(/\//g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .join('_')
    .replace(/[^A-Za-z0-9_.-]/g, '')
    .replace(/^[._]+|[._]+$/g, '');
}

/**
 * Folder calls on the Files API, in the shape {@link ensureFolderPath} takes.
 *
 * @param {{get: Function, post: Function}} http  axios or a compatible client
 * @param {string} filesApiBase  e.g. "/api/files"
 */
export function filesFolderApi(http, filesApiBase) {
  return {
    listFolders: async (path) => {
      const res = await http.get(`${filesApiBase}/browse`, { params: { path, fields: 'light' } });
      return res.data?.data?.folders || [];
    },
    createFolder: async (name, parentPath) => {
      const res = await http.post(`${filesApiBase}/folder`, { name, parent_path: parentPath });
      return res.data?.data;
    },
  };
}

const isConflict = (err) => err?.response?.status === 409;

/**
 * Make sure a nested folder path exists under `baseFolder`, creating what is
 * missing, and return the stored path of the deepest folder.
 *
 * Existing folders are matched by their dropped name or by the name the server
 * stores it under, so every file of a dropped "Weekend in Lisbon" lands in the
 * same "Weekend_in_Lisbon" folder. Pass one `cache` Map for a whole drop so each
 * folder is looked up once.
 *
 * @param {string} relativePath  folder path relative to `baseFolder`, `/`-separated
 * @param {string} baseFolder    stored path to start from ("/" for the root)
 * @param {{listFolders: Function, createFolder: Function}} api  see {@link filesFolderApi}
 * @param {Map<string, string>} [cache]
 * @returns {Promise<string>}
 */
export async function ensureFolderPath(relativePath, baseFolder, api, cache = new Map()) {
  if (!relativePath || relativePath === '/') return baseFolder;
  let current = baseFolder;
  for (const part of relativePath.split('/').filter(Boolean)) {
    const key = `${current}\n${part}`;
    if (cache.has(key)) {
      current = cache.get(key);
      continue;
    }
    const stored = storedFolderName(part);
    const find = (folders) => folders.find((f) => f.name === part || (stored && f.name === stored));
    let folder = find(await api.listFolders(current));
    if (!folder) {
      try {
        folder = await api.createFolder(part, current);
      } catch (err) {
        if (!isConflict(err)) throw err;
        folder = find(await api.listFolders(current));
        if (!folder) throw err;
      }
    }
    cache.set(key, folder.path);
    current = folder.path;
  }
  return current;
}
