import { describe, it, expect, beforeEach, vi } from "vitest";
import { sendQuery, CODE_EDITOR_SESSION_ID } from "../chatService";

const jsonResponse = (body) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

beforeEach(() => {
  global.fetch = vi.fn(async () => jsonResponse({ response: "ok" }));
});

const sentPayload = () => JSON.parse(global.fetch.mock.calls[0][1].body);

describe("sendQuery session ids", () => {
  it("files the Code Editor assistant's traffic under its own session", async () => {
    const result = await sendQuery("explain this file", ["coding", "assistance", "python"], null, CODE_EDITOR_SESSION_ID, false);
    expect(result).toEqual({ response: "ok" });
    expect(global.fetch.mock.calls[0][0]).toMatch(/\/query$/);
    expect(sentPayload().session_id).toBe(CODE_EDITOR_SESSION_ID);
    expect(CODE_EDITOR_SESSION_ID).toMatch(/^[a-zA-Z0-9]+$/);
  });

  it("sends no session id when none is given, which the backend files under its default session", async () => {
    await sendQuery("hello", ["coding"], null, null, false);
    expect(sentPayload()).not.toHaveProperty("session_id");
  });
});
