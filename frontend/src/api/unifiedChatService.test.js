import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("./apiClient", () => ({ BASE_URL: "/api" }));
vi.mock("../utils/debugLog", () => ({ debugLog: () => {} }));
vi.mock("../stores/useAppStore", () => ({
  useAppStore: {
    getState: () => ({
      agentScreenOpen: false,
      getSessionMode: () => "chat",
      getSessionThinking: () => undefined,
      chatImageModel: "auto",
    }),
  },
}));

import UnifiedChatService from "./unifiedChatService";

const sentBody = () => JSON.parse(global.fetch.mock.calls[0][1].body);

beforeEach(() => {
  global.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ success: true }) }));
});

describe("sendMessage pictures", () => {
  it("sends one picture as image", async () => {
    await new UnifiedChatService(null).sendMessage("s1", "what is this?", {}, "AAAA");
    const body = sentBody();
    expect(body.image).toBe("AAAA");
    expect(body.images).toBeUndefined();
  });

  it("sends several pictures once each, as images, primary first", async () => {
    await new UnifiedChatService(null).sendMessage("s1", "compare these", {}, ["AAAA", "BBBB", "CCCC"]);
    const body = sentBody();
    expect(body.images).toEqual(["AAAA", "BBBB", "CCCC"]);
    // The server reads images first and falls back to image, so the first
    // picture is not sent a second time.
    expect(body.image).toBeUndefined();
  });

  it("sends a one-entry list as image and nothing for no picture", async () => {
    await new UnifiedChatService(null).sendMessage("s1", "one", {}, ["AAAA"]);
    expect(sentBody()).toMatchObject({ image: "AAAA" });
    expect(sentBody().images).toBeUndefined();

    global.fetch.mockClear();
    await new UnifiedChatService(null).sendMessage("s1", "none", {}, null);
    expect(sentBody().image).toBeUndefined();
    expect(sentBody().images).toBeUndefined();
  });
});
