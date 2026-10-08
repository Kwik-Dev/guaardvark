// frontend/src/components/agents/AgentTile.jsx
// One agent on the Agents page: a dashboard tile that opens the editor on click
// and hands right-clicks to the page's menu.

import React from "react";
import { Box } from "@mui/material";
import { DashboardTile } from "../settings/ui";

/** Fields a person changed from the built-in default, not counting the on/off switch. */
export const agentEdits = (agent) => (agent?.overridden || []).filter((field) => field !== "enabled");

const isOrchestrator = (agent) => agent?.agent_type === "orchestrator";

/** ok when it can run, warn when something it needs is missing, off when switched off. */
export function agentTone(agent) {
  if (!agent.enabled) return "off";
  if (agent.unavailable_reason || (agent.tools_missing || []).length) return "warn";
  return "ok";
}

/** The tile's second line: why it cannot run, or its tool count, limit, model and edits. */
export function agentSub(agent) {
  if (!agent.enabled) return "Off";
  if (agent.unavailable_reason) return agent.unavailable_reason;
  if (isOrchestrator(agent)) return "Hands steps to the other agents";
  const tools = (agent.tools || []).length;
  const missing = (agent.tools_missing || []).length;
  const parts = [`${tools} tool${tools === 1 ? "" : "s"}`];
  if (missing) parts.push(`${missing} missing`);
  parts.push(`max ${agent.max_iterations}`);
  if (agent.model) parts.push(agent.model);
  if (agentEdits(agent).length) parts.push("edited");
  return parts.join(" · ");
}

/**
 * @param {object}   agent          an entry from GET /api/agents
 * @param {function} onOpen         called with the agent on click or Enter
 * @param {function} onContextMenu  called with (event, agent) on right-click
 */
const AgentTile = ({ agent, onOpen, onContextMenu }) => (
  <Box
    data-testid={`agent-tile-${agent.id}`}
    onContextMenu={(event) => onContextMenu?.(event, agent)}
    sx={{ display: "grid", minWidth: 0 }}
  >
    <DashboardTile
      label={agent.name}
      help={agent.description}
      tone={agentTone(agent)}
      value={agent.summary || agent.description}
      sub={agentSub(agent)}
      onClick={() => onOpen?.(agent)}
    />
  </Box>
);

export default AgentTile;
