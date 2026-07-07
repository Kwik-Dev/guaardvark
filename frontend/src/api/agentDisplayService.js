// Agent Display dependency detector + installer + start/stop controls.
// Mirrors voiceService.installWhisper for the virtual display stack.

import { BASE_URL, handleResponse } from './apiClient';

const ROOT = `${BASE_URL}/agent-control`;

export async function getDisplayStatus() {
  const response = await fetch(`${ROOT}/display-status`);
  return handleResponse(response);
}

export async function installDisplay() {
  const response = await fetch(`${ROOT}/install-display`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  return handleResponse(response);
}

export async function startDisplay() {
  const response = await fetch(`${ROOT}/start-display`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  return handleResponse(response);
}

export async function stopDisplay({ force = false } = {}) {
  const response = await fetch(`${ROOT}/stop-display`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ force }),
  });
  return handleResponse(response);
}

export async function armDisplayIdle(seconds = 300) {
  const response = await fetch(`${ROOT}/display-idle-arm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ seconds }),
  });
  return handleResponse(response);
}

export async function cancelDisplayIdle() {
  const response = await fetch(`${ROOT}/display-idle-disarm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  return handleResponse(response);
}

export default {
  getDisplayStatus,
  installDisplay,
  startDisplay,
  stopDisplay,
  armDisplayIdle,
  cancelDisplayIdle,
};
