import { describe, expect, it } from "vitest";
import { applyApprovalRequest, applyToolCall } from "../approvalCards";

const consentRequest = {
  tools: ["generate_identity"],
  consent: true,
  tool_details: [
    {
      tool: "generate_identity",
      consent: true,
      params: { prompt: "in a greenhouse" },
      reference_image_url: "/api/uploads/face.png",
    },
  ],
};

describe("approval cards", () => {
  it("marks the pending card when the loop announced the tool first", () => {
    const prev = applyToolCall([], { tool: "generate_identity", params: { prompt: "x" } });
    const next = applyApprovalRequest(prev, consentRequest);
    expect(next).toHaveLength(1);
    expect(next[0].requiresApproval).toBe(true);
    expect(next[0].consent).toBe(true);
  });

  it("creates the card when the direct path asks before announcing", () => {
    const next = applyApprovalRequest([], consentRequest);
    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({
      tool: "generate_identity",
      isPending: true,
      awaitingCall: true,
      requiresApproval: true,
      consent: true,
      consentPrompt: "in a greenhouse",
    });
  });

  it("fills that card in when the tool is announced after a yes", () => {
    const asked = applyApprovalRequest([], consentRequest);
    const next = applyToolCall(asked, { tool: "generate_identity", params: { seed: 1 } });
    expect(next).toHaveLength(1);
    expect(next[0].awaitingCall).toBe(false);
    expect(next[0].requiresApproval).toBe(false);
    expect(next[0].params).toMatchObject({ prompt: "in a greenhouse", seed: 1 });
  });

  it("still appends an ordinary announced tool", () => {
    const next = applyToolCall([{ tool: "a", isPending: false }], { tool: "b" });
    expect(next.map((c) => c.tool)).toEqual(["a", "b"]);
  });
});
