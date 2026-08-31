import { useCallback, useEffect, useRef, useState } from "react";
import { fetchPublishes } from "../api/connectionsService";

// A pending publish has no Task row until it is approved, so it is invisible to
// the jobs API. The queue and the sidebar badge both read the publish records
// directly, and share this hook so they cannot disagree about the count.
const POLL_MS = 30000;
const LIMIT = 200;

/**
 * Track publishes awaiting approval.
 *
 * @param {object}  options
 * @param {boolean} options.notify  raise a desktop notification when the count rises
 * @returns {{count: number, pending: object[], loading: boolean, error: string|null, refresh: function}}
 */
export const usePendingApprovals = ({ notify = false } = {}) => {
  const [pending, setPending] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // Notify on a rising edge only — a poll that finds the same queue is not news.
  const previousCount = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const rows = await fetchPublishes({
        status: "awaiting_approval",
        limit: LIMIT,
      });
      setPending(rows);
      setError(null);
      return rows;
    } catch (err) {
      setError(err?.message || "Could not load pending approvals");
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    const tick = async () => {
      const rows = await refresh();
      if (!active || rows === null) return;
      const previous = previousCount.current;
      previousCount.current = rows.length;
      if (notify && previous !== null && rows.length > previous) {
        raiseDesktopNotification(rows.length - previous, rows);
      }
    };
    tick();
    const timer = setInterval(tick, POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [refresh, notify]);

  return { count: pending.length, pending, loading, error, refresh };
};

/** Best-effort desktop notification. Silent when unsupported or not granted. */
function raiseDesktopNotification(added, rows) {
  if (typeof window === "undefined" || !("Notification" in window)) return;
  if (Notification.permission !== "granted") return;
  const newest = rows[0];
  const detail = newest
    ? `${newest.platform}${newest.title ? ` · ${newest.title}` : ""}`
    : "";
  try {
    new Notification(
      added === 1 ? "Publish awaiting approval" : `${added} publishes awaiting approval`,
      { body: detail, tag: "guaardvark-publish-approvals" },
    );
  } catch {
    // Some browsers throw for constructed notifications outside a service
    // worker. The badge is the guaranteed signal; this is an enhancement.
  }
}

/** True once the browser will actually show notifications. */
export const desktopNotificationsAvailable = () =>
  typeof window !== "undefined" && "Notification" in window;

export const desktopNotificationsGranted = () =>
  desktopNotificationsAvailable() && Notification.permission === "granted";

/** Ask for permission. Must be called from a user gesture, never on load. */
export const requestDesktopNotifications = async () => {
  if (!desktopNotificationsAvailable()) return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  const result = await Notification.requestPermission();
  return result === "granted";
};
