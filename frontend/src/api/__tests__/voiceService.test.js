import { describe, it, expect, beforeEach, vi } from "vitest";

const handlers = {};
const emitted = [];
vi.mock("socket.io-client", () => ({
  io: () => ({
    connected: true,
    on: (event, fn) => {
      handlers[event] = fn;
    },
    emit: (event, payload) => emitted.push([event, payload]),
  }),
}));

import voiceService from "../voiceService";

beforeEach(() => {
  emitted.length = 0;
});

describe("voiceService socket streams", () => {
  it("a transcript reaches the callback of the stream it belongs to", () => {
    const first = vi.fn();
    const second = vi.fn();
    const firstId = voiceService.startVoiceStream("s", first);
    voiceService.stopVoiceStream(firstId);
    // The next stream starts before the server has answered the first.
    const secondId = voiceService.startVoiceStream("s", second);
    expect(secondId).not.toBe(firstId);

    handlers["voice:final_transcript"]({ session_id: firstId, text: "one" });
    expect(first).toHaveBeenCalledWith("one");
    expect(second).not.toHaveBeenCalled();

    handlers["voice:final_transcript"]({ session_id: secondId, text: "two" });
    expect(second).toHaveBeenCalledWith("two");
    expect(emitted.filter(([e]) => e === "voice:stream_end")).toEqual([
      ["voice:stream_end", { session_id: firstId }],
    ]);
  });
});

describe("voiceService playback", () => {
  it("stopPlayback ends the wait of whoever is playing", async () => {
    class FakeAudio {
      constructor() {
        this.paused = false;
      }
      play() {
        return Promise.resolve();
      }
      pause() {
        this.paused = true;
      }
      removeAttribute() {}
      load() {}
    }
    vi.stubGlobal("Audio", FakeAudio);
    const playing = voiceService.playAudio("/api/voice/audio/x.wav", { enableVisualization: false });
    await Promise.resolve();
    expect(voiceService.getIsTTSPlaying()).toBe(true);
    const epoch = voiceService.playbackEpoch;
    voiceService.stopPlayback();
    await expect(playing).resolves.toBeUndefined();
    expect(voiceService.getIsTTSPlaying()).toBe(false);
    expect(voiceService.playbackEpoch).toBe(epoch + 1);
    vi.unstubAllGlobals();
  });
});

describe("voiceService.transcribeUtterance", () => {
  it("posts one recording to speech-to-text and returns the text", async () => {
    fetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      text: async () => JSON.stringify({ text: "hello" }),
    });
    const result = await voiceService.transcribeUtterance(new Blob(["x"], { type: "audio/webm;codecs=opus" }));
    expect(result).toEqual({ text: "hello" });
    const [url, init] = fetch.mock.calls.at(-1);
    expect(url).toBe("/api/voice/speech-to-text");
    expect(init.method).toBe("POST");
    expect(init.body.get("audio").name).toBe("utterance.webm");
  });

  it("rejects with the status so the session can tell silence from a missing model", async () => {
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      statusText: "Conflict",
      url: "/api/voice/speech-to-text",
      headers: { get: () => "application/json" },
      text: async () => JSON.stringify({ error: "Install the speech model to use voice", code: "SPEECH_MODEL_MISSING" }),
    });
    await expect(voiceService.transcribeUtterance(new Blob(["x"], { type: "audio/mp4" }))).rejects.toMatchObject({
      status: 409,
      message: "Install the speech model to use voice",
    });
    expect(fetch.mock.calls.at(-1)[1].body.get("audio").name).toBe("utterance.mp4");
  });
});
