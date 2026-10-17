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
  if (u.startsWith("data:") || u.startsWith("blob:")) return true;
  // Resolve before judging: "//host/x" and "/\host/x" look relative, but the
  // browser fetches both from another host.
  let host;
  try {
    host = new URL(u, window.location.origin).hostname;
  } catch {
    // The browser cannot resolve it either, so there is nothing to load.
    return false;
  }
  if (!host) return true;
  if (host === window.location.hostname) return true;
  if (host === "127.0.0.1" || host === "localhost" || host === "[::1]") return true;
  // Private LAN ranges — the app is intentionally reachable on the local network.
  // IPv4 literals only: "10.example.com" is a public name.
  const ip = host.match(/^(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}$/);
  if (!ip) return false;
  const a = Number(ip[1]);
  const b = Number(ip[2]);
  return a === 10 || (a === 192 && b === 168) || (a === 172 && b >= 16 && b <= 31);
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
