// frontend/src/api/mcpService.js
// MCP (Model Context Protocol) server management API
/* eslint-env browser */

import { BASE_URL, handleResponse } from "./apiClient";

const MCP_BASE = `${BASE_URL}/automation/mcp`;

const json = (method, body) => ({
  method,
  headers: { "Content-Type": "application/json" },
  ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
});

export const getMcpStatus = async () =>
  handleResponse(await fetch(`${MCP_BASE}/status`, json("GET")));

export const listMcpServers = async () =>
  handleResponse(await fetch(`${MCP_BASE}/servers`, json("GET")));

export const getMcpServer = async (name) =>
  handleResponse(await fetch(`${MCP_BASE}/servers/${encodeURIComponent(name)}`, json("GET")));

/**
 * Create or replace a server definition. For env, send "***" to keep an
 * existing secret value (the API never returns secret values).
 */
export const saveMcpServer = async (name, definition) =>
  handleResponse(
    await fetch(`${MCP_BASE}/servers/${encodeURIComponent(name)}`, json("PUT", definition)),
  );

export const deleteMcpServer = async (name) =>
  handleResponse(await fetch(`${MCP_BASE}/servers/${encodeURIComponent(name)}`, json("DELETE")));

export const connectMcpServer = async (server) =>
  handleResponse(await fetch(`${MCP_BASE}/connect`, json("POST", { server })));

export const disconnectMcpServer = async (server) =>
  handleResponse(await fetch(`${MCP_BASE}/disconnect`, json("POST", { server })));

export const reloadMcpConfig = async () =>
  handleResponse(await fetch(`${MCP_BASE}/reload-config`, json("POST", {})));

export const listMcpTools = async (server) =>
  handleResponse(
    await fetch(`${MCP_BASE}/tools${server ? `?server=${encodeURIComponent(server)}` : ""}`, json("GET")),
  );

export const executeMcpTool = async (server, tool, args = {}) =>
  handleResponse(
    await fetch(`${MCP_BASE}/execute`, json("POST", { server, tool, arguments: args, caller: "ui" })),
  );

export const getMcpAuditLog = async (limit = 50) =>
  handleResponse(await fetch(`${MCP_BASE}/audit-log?limit=${limit}`, json("GET")));
