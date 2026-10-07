import React from "react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render } from "@testing-library/react";

vi.mock("../HealthContext", () => ({ useHealth: () => ({ isBackendOffline: false }) }));

import { VoiceProvider, useVoice } from "../VoiceContext";

const json = (body) => ({
  ok: true,
  status: 200,
  headers: { get: () => "application/json" },
  text: async () => JSON.stringify(body),
});

let getUserMedia;
let seen;

function Probe() {
  seen = useVoice();
  return null;
}

beforeEach(() => {
  getUserMedia = vi.fn(async () => ({ getTracks: () => [] }));
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
  Object.defineProperty(navigator, "permissions", {
    configurable: true,
    value: { query: vi.fn(async () => ({ state: "prompt" })) },
  });
  fetch.mockReset();
  fetch.mockImplementation(async (url) =>
    json(String(url).includes("/voices") ? { voices: [{ id: "libritts", name: "LibriTTS" }] } : { status: "available" })
  );
});

afterEach(() => {
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: undefined });
  Object.defineProperty(navigator, "permissions", { configurable: true, value: undefined });
});

describe("VoiceProvider at app start", () => {
  it("reads the microphone permission without asking for it", async () => {
    await act(async () => {
      render(
        <VoiceProvider>
          <Probe />
        </VoiceProvider>
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(navigator.permissions.query).toHaveBeenCalledWith({ name: "microphone" });
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(seen.micPermissionState).toBe("prompt");
  });

  it("on a plain-http LAN address it reports why the mic is unavailable", async () => {
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: undefined });
    vi.stubGlobal("isSecureContext", false);
    await act(async () => {
      render(
        <VoiceProvider>
          <Probe />
        </VoiceProvider>
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(seen.micPermissionError).toBe("Mic needs HTTPS or localhost");
    vi.unstubAllGlobals();
  });
});
