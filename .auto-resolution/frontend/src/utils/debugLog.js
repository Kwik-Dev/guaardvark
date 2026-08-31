/**
 * Dev-only debug logger.
 * Mirrors the inline pattern used across the frontend.
 * Safe to import everywhere; no-ops in production builds.
 *
 * Usage:
 *   import { debugLog } from '../utils/debugLog';
 *   debugLog('[MyComponent]', 'state=', foo);
 */
export const debugLog = (...args) => {
  if (import.meta.env.DEV) {
    console.debug(...args);
  }
};

export default debugLog;
