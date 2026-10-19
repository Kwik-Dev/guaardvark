import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { stopChatTurn } from "../chatStopService";

const json = (status, body) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("stopChatTurn", () => {
  const realFetch = global.fetch;
  afterEach(() => {
    global.fetch = realFetch;
  });

  beforeEach(() => {
    global.fetch = vi.fn();
  });

  it("posts the abort and the agent kill, and reports success", async () => {
    global.fetch.mockImplementation(async () => json(200, { success: true }));
    await expect(stopChatTurn("s 1")).resolves.toEqual({ ok: true });
    const urls = global.fetch.mock.calls.map(([url, init]) => [url, init.method]);
    expect(urls).toEqual([
      ["/api/chat/unified/s%201/abort", "POST"],
      ["/api/agent-control/kill", "POST"],
    ]);
  });

  it("says Stop did not reach the turn when the abort is refused", async () => {
    global.fetch.mockImplementation(async (url) =>
      String(url).includes("/abort")
        ? json(500, { error: "session lock busy" })
        : json(200, { success: true }),
    );
    const result = await stopChatTurn("s1");
    expect(result.ok).toBe(false);
    expect(result.error).toBe(
      "Stop did not reach the chat turn: session lock busy. It may still be running.",
    );
  });

  it("names an unreachable backend instead of a raw network error", async () => {
    global.fetch.mockImplementation(async () => json(502, { error: "backend_offline" }));
    const result = await stopChatTurn("s1");
    expect(result).toEqual({
      ok: false,
      error:
        "Stop did not reach the chat turn and the agent task: the backend is not reachable. It may still be running.",
    });
  });

  it("never throws when fetch itself rejects", async () => {
    global.fetch.mockRejectedValue(new TypeError("Failed to fetch"));
    const result = await stopChatTurn("s1");
    expect(result.ok).toBe(false);
    expect(result.error).toContain("Failed to fetch");
  });
});
