// Which "Did you know" tip to show next, and what this browser has already seen.
// Tip text lives in config/tips.js; the card is DidYouKnowTip.jsx.

export const SEEN_TIPS_KEY = "guaardvark:tips-seen";
export const SESSION_TIP_KEY = "guaardvark:tip-shown";

// Fallback for a browser that blocks sessionStorage: one tip per page load instead.
let shownWithoutStorage = false;

function storage(kind) {
  try {
    return typeof window !== "undefined" ? window[kind] : null;
  } catch {
    return null;
  }
}

/** Ids of the tips this browser has shown, oldest first. Never throws. */
export function readSeenTips() {
  try {
    const raw = storage("localStorage")?.getItem(SEEN_TIPS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((id) => typeof id === "string") : [];
  } catch {
    return [];
  }
}

/** Record that a tip was shown: it moves to the end of the seen list. Returns the new list. */
export function markTipSeen(id) {
  const seen = readSeenTips().filter((s) => s !== id);
  seen.push(id);
  try {
    storage("localStorage")?.setItem(SEEN_TIPS_KEY, JSON.stringify(seen));
  } catch {
    // Storage full or blocked: the tip may come round again sooner.
  }
  return seen;
}

/** Whether a tip has already appeared in this browser tab's session. */
export function tipShownThisSession() {
  try {
    const session = storage("sessionStorage");
    if (session) return session.getItem(SESSION_TIP_KEY) === "1" || shownWithoutStorage;
  } catch {
    // fall through to the in-memory flag
  }
  return shownWithoutStorage;
}

export function markTipShownThisSession() {
  shownWithoutStorage = true;
  try {
    storage("sessionStorage")?.setItem(SESSION_TIP_KEY, "1");
  } catch {
    // the in-memory flag covers this page load
  }
}

/** Test seam: forget the in-memory session flag. */
export function resetTipSessionForTests() {
  shownWithoutStorage = false;
}

function routeHidden(tip, hiddenRoutes) {
  if (!tip.route || !hiddenRoutes || hiddenRoutes.length === 0) return false;
  const path = tip.route.split(/[?#]/)[0];
  return hiddenRoutes.includes(path) || (path === "/dashboard" && hiddenRoutes.includes("/"));
}

/**
 * The tip to show next: the first one not yet seen, otherwise the one seen
 * longest ago. Tips whose page the active profile hides are skipped.
 *
 * @param {Array<{id: string, route?: string}>} tips
 * @param {string[]} seen          ids, oldest first (readSeenTips)
 * @param {{hiddenRoutes?: string[], excludeId?: string}} [options]
 *   `excludeId` skips the tip on screen now, so "Next tip" never repeats it.
 * @returns {object|null}
 */
export function pickTip(tips, seen = [], { hiddenRoutes = [], excludeId = null } = {}) {
  const eligible = (tips || []).filter((tip) => tip && tip.id !== excludeId && !routeHidden(tip, hiddenRoutes));
  if (eligible.length === 0) return null;
  const order = new Map(seen.map((id, i) => [id, i]));
  const unseen = eligible.find((tip) => !order.has(tip.id));
  if (unseen) return unseen;
  return eligible.reduce((oldest, tip) => (order.get(tip.id) < order.get(oldest.id) ? tip : oldest));
}
