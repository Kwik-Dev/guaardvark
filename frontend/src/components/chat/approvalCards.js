/**
 * Tool-call card state for chat:tool_approval_request and chat:tool_call.
 *
 * The agent loop announces a tool (chat:tool_call) and then asks for approval,
 * so the request finds its card already on screen. The direct path (slash
 * commands, "put this person in…") asks first and announces only after a yes;
 * a decline never announces the tool at all. A request that names a tool with
 * no pending card therefore creates the card itself, marked `awaitingCall`, and
 * the chat:tool_call that follows an approval fills that card in rather than
 * adding a second one.
 */

import { parseConsentApproval } from "./consentApproval";

function withApproval(card, data) {
  const parsed = parseConsentApproval(data, card.tool, card.params);
  return {
    ...card,
    requiresApproval: true,
    params: parsed.params,
    consent: parsed.consent,
    consentImage: parsed.image,
    consentPrompt: parsed.prompt,
  };
}

export function applyApprovalRequest(prev, data) {
  const tools = Array.isArray(data?.tools) ? data.tools : [];
  const wanted = new Set(tools);
  const covered = new Set();
  const next = prev.map((tc) => {
    if (tc.isPending && wanted.has(tc.tool)) {
      covered.add(tc.tool);
      return withApproval(tc, data);
    }
    return tc;
  });
  for (const tool of tools) {
    if (covered.has(tool)) continue;
    covered.add(tool);
    next.push(
      withApproval(
        {
          tool,
          params: {},
          result: null,
          durationMs: null,
          isPending: true,
          awaitingCall: true,
        },
        data,
      ),
    );
  }
  return next;
}

export function applyToolCall(prev, data) {
  const params = data?.params || data?.arguments || data?.args || {};
  for (let i = prev.length - 1; i >= 0; i--) {
    if (prev[i].awaitingCall && prev[i].tool === data?.tool) {
      const next = [...prev];
      next[i] = {
        ...prev[i],
        params: { ...prev[i].params, ...params },
        reasoning: data.reasoning,
        awaitingCall: false,
        requiresApproval: false,
      };
      return next;
    }
  }
  return [
    ...prev,
    {
      tool: data?.tool,
      params,
      result: null,
      durationMs: null,
      isPending: true,
      reasoning: data?.reasoning,
    },
  ];
}
