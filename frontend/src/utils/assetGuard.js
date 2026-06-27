// frontend/src/utils/assetGuard.js
// Offline-first asset guard. A stale or hostile REMOTE image URL stored in chat
// history (e.g. an expired files.oaiusercontent.com link from an old ChatGPT paste)
// must NEVER cause the browser to phone out when a message is rendered. This blocks
// any media URL pointing at a public (non-local) host; local, LAN, relative, data:
// and blob: URLs pass through untouched.
/* eslint-env browser */

export function isLocalAssetUrl(url) {
  if (!url || typeof url !== "string") return false;
  const u = url.trim();
  if (!u) return false;
  // Relative paths + inline data are always local.
  if (u.startsWith("/") || u.startsWith("./") || u.startsWith("../")) return true;
  if (u.startsWith("data:") || u.startsWith("blob:")) return true;
  let host;
  try {
    host = new URL(u, window.location.origin).hostname;
  } catch {
    // Unparseable but carries no scheme → treat as a relative/local reference.
    return !/^[a-z][a-z0-9+.-]*:\/\//i.test(u);
  }
  if (!host) return true;
  if (host === window.location.hostname) return true;
  if (host === "127.0.0.1" || host === "localhost" || host === "::1") return true;
  // Private LAN ranges — the app is intentionally reachable on the local network.
  if (/^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host)) return true;
  return false;
}

// Inline SVG shown in place of a blocked remote image. Pure data URI — no network.
export const REMOTE_BLOCKED_PLACEHOLDER =
  "data:image/svg+xml;charset=utf8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="120">' +
      '<rect width="100%" height="100%" fill="#2b2b2b"/>' +
      '<text x="50%" y="46%" fill="#ff8a80" font-family="sans-serif" font-size="12" ' +
      'text-anchor="middle">remote image blocked</text>' +
      '<text x="50%" y="64%" fill="#9e9e9e" font-family="sans-serif" font-size="10" ' +
      'text-anchor="middle">(offline-first)</text>' +
    "</svg>"
  );

// The only safe accessor: returns the URL if it is local, else the offline
// placeholder so the <img>/<video> never issues an outbound request.
export function guardedMediaSrc(url) {
  return isLocalAssetUrl(url) ? url : REMOTE_BLOCKED_PLACEHOLDER;
}
