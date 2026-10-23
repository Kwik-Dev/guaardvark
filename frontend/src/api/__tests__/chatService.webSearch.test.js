import { describe, it, expect, beforeEach, vi } from "vitest";
import { sendChatMessage } from "../chatService";

// The browser fallback (socket down) sends the message to enhanced chat as
// typed and never searches the web by itself; the reply's web_search_offer is
// what lets the person search, on a click.

const jsonResponse = (body) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

const OFFER = { action: "search", query: "when did the war end?" };

beforeEach(() => {
  global.fetch = vi.fn(async () =>
    jsonResponse({
      success: true,
      data: {
        response: "It ended in 1945.",
        user_message_id: 7,
        web_search_used: false,
        web_search_offer: OFFER,
      },
    })
  );
});

describe("sendChatMessage web search", () => {
  it("sends no search of its own, only the message", async () => {
    await sendChatMessage("s1", "when did the war end?");
    const urls = global.fetch.mock.calls.map(([url]) => String(url));
    expect(urls.some((url) => url.includes("web-search"))).toBe(false);
    expect(urls).toHaveLength(1);
    expect(urls[0]).toMatch(/\/enhanced-chat$/);
    expect(JSON.parse(global.fetch.mock.calls[0][1].body).message).toBe("when did the war end?");
  });

  it("passes the reply's offer through", async () => {
    const result = await sendChatMessage("s2", "what company makes this?");
    expect(result.webSearchOffer).toEqual(OFFER);
    expect(result.webSearchUsed).toBe(false);
  });
});
