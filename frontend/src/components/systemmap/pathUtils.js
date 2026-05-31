// frontend/src/components/systemmap/pathUtils.js
//
// Shared path/module helpers for the System Map. Lifted out of SystemMapCanvas
// so SystemMapPage can derive a node's section from node_meta[id].path the same
// way the canvas does — keeping the two in lockstep (one source of truth for the
// section-from-path mapping that drives both hue and the detail-panel chips).

// Convert "backend/services/foo.py" → "backend/services" (or other section).
export function pathToSection(path) {
  if (!path || typeof path !== "string") return "other";
  if (!path.includes("/")) return "top-level";
  if (path.startsWith("backend/")) {
    const sub = path.slice("backend/".length).split("/")[0];
    return `backend/${sub}`;
  }
  if (path.startsWith("frontend/src/")) {
    const sub = path.slice("frontend/src/".length).split("/")[0];
    return `frontend/${sub}`;
  }
  if (path.startsWith("frontend/")) return "frontend/other";
  if (path.startsWith("plugins/")) return "plugins";
  if (path.startsWith("scripts/")) return "scripts";
  if (path.startsWith("cli/")) return "cli";
  if (path.startsWith("training/")) return "training";
  return "other";
}

// Module name "backend.services.foo" → path "backend/services/foo.py".
export function moduleNameToPath(name) {
  if (!name) return "";
  return name.replace(/\./g, "/") + ".py";
}
