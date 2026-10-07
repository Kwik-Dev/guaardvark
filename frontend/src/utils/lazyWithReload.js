// Code that changed on disk after this tab loaded (a sync, a pull, a rebuild) can
// fail to import; one reload fixes that, a second failure is a real fault.

import { lazy } from "react";

const STALE_MODULE_ERROR =
  /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Outdated Optimize Dep/i;

const RELOAD_STAMP_KEY = "guaardvark:stale-module-reload-at";
export const RELOAD_WINDOW_MS = 30_000;

const defaultReload = () => window.location.reload();

const defaultStorage = () => {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
};

/**
 * True when an error means the browser is holding code older than the server's.
 * @param {unknown} error
 * @returns {boolean}
 */
export function isStaleModuleError(error) {
  const message = typeof error === "string" ? error : error?.message;
  return typeof message === "string" && STALE_MODULE_ERROR.test(message);
}

/**
 * Reload the page unless this tab already did so within the last
 * RELOAD_WINDOW_MS. Without session storage there is no loop guard, so it
 * does not reload at all.
 * @param {{reload?: () => void, storage?: Storage|null, now?: number}} [options]
 * @returns {boolean} true when a reload was started
 */
export function reloadOnce({ reload = defaultReload, storage = defaultStorage(), now = Date.now() } = {}) {
  if (!storage) return false;
  try {
    const last = Number(storage.getItem(RELOAD_STAMP_KEY)) || 0;
    if (now - last < RELOAD_WINDOW_MS) return false;
    storage.setItem(RELOAD_STAMP_KEY, String(now));
  } catch {
    return false;
  }
  reload();
  return true;
}

/**
 * Guarded reload for errors that mean stale code; other errors are left alone.
 * @param {unknown} error
 * @param {Parameters<typeof reloadOnce>[0]} [options]
 * @returns {boolean} true when a reload was started
 */
export function reloadOnceForStaleModule(error, options) {
  return isStaleModuleError(error) && reloadOnce(options);
}

/**
 * React.lazy that reloads the page once when the module cannot be fetched
 * because the code changed underneath the tab. While the reload is under way
 * the import never settles, so Suspense keeps its fallback on screen.
 * @param {() => Promise<{default: React.ComponentType<any>}>} factory
 * @param {Parameters<typeof reloadOnce>[0]} [options]
 */
export default function lazyWithReload(factory, options) {
  return lazy(() =>
    factory().catch((error) => {
      if (reloadOnceForStaleModule(error, options)) return new Promise(() => {});
      throw error;
    }),
  );
}
