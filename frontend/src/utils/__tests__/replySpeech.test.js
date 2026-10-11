import { describe, it, expect, vi } from "vitest";
import {
  NO_VOICE_INSTALLED_MESSAGE,
  chunkForSpeech,
  describeSpeakError,
  speakInChunks,
} from "../replySpeech";

describe("chunkForSpeech", () => {
  it("keeps every sentence, three to a chunk", () => {
    const text = "One. Two! Three? Four. Five.";
    expect(chunkForSpeech(text)).toEqual(["One. Two! Three?", "Four. Five."]);
  });

  it("starts a new chunk before the character limit and never splits a sentence", () => {
    const long = "A".repeat(300) + ".";
    const chunks = chunkForSpeech(`Short one. ${long} Short two.`, { maxChars: 120 });
    expect(chunks).toEqual(["Short one.", long, "Short two."]);
  });

  it("speaks text without end punctuation and nothing for empty text", () => {
    expect(chunkForSpeech("just words")).toEqual(["just words"]);
    expect(chunkForSpeech("")).toEqual([]);
    expect(chunkForSpeech(null)).toEqual([]);
  });
});

describe("speakInChunks", () => {
  const audioFor = (chunk) => `url:${chunk}`;

  it("synthesizes the next chunk while the current one plays and plays all in order", async () => {
    const events = [];
    const synthesize = vi.fn(async (chunk) => {
      events.push(`synth ${chunk}`);
      return audioFor(chunk);
    });
    const play = vi.fn(async (url) => {
      events.push(`play ${url}`);
    });
    const result = await speakInChunks(["a", "b", "c"], { synthesize, play });
    expect(result).toEqual({ spoken: 3, total: 3, cancelled: false, error: null });
    expect(events).toEqual(["synth a", "synth b", "play url:a", "synth c", "play url:b", "play url:c"]);
  });

  it("stops after a cancel and reports how much was spoken", async () => {
    let cancelled = false;
    const play = vi.fn(async () => {
      cancelled = true;
    });
    const result = await speakInChunks(["a", "b"], { synthesize: async (c) => audioFor(c), play, isCancelled: () => cancelled });
    expect(result).toEqual({ spoken: 1, total: 2, cancelled: true, error: null });
    expect(play).toHaveBeenCalledTimes(1);
  });

  it("returns the error that stopped it, with the count spoken so far", async () => {
    const synthesize = vi.fn(async (chunk) => {
      if (chunk === "b") throw new Error("engine down");
      return audioFor(chunk);
    });
    const play = vi.fn(async () => {});
    const result = await speakInChunks(["a", "b", "c"], { synthesize, play });
    expect(result.spoken).toBe(1);
    expect(result.total).toBe(3);
    expect(result.error.message).toBe("engine down");
    expect(play).toHaveBeenCalledTimes(1);
  });

  it("does nothing for no chunks", async () => {
    const synthesize = vi.fn();
    expect(await speakInChunks([], { synthesize, play: vi.fn() })).toEqual({ spoken: 0, total: 0, cancelled: false, error: null });
    expect(synthesize).not.toHaveBeenCalled();
  });
});

describe("describeSpeakError", () => {
  it("names the install step only when the backend says no voice is installed", () => {
    expect(describeSpeakError({ data: { code: "no_voice_installed" } })).toBe(NO_VOICE_INSTALLED_MESSAGE);
    expect(NO_VOICE_INSTALLED_MESSAGE).toContain("Voice Settings");
    expect(NO_VOICE_INSTALLED_MESSAGE).toContain("Kokoro voice pack");
    // A proxy or task-queue 503 during a restart is not a missing voice.
    expect(describeSpeakError({ status: 503, message: "Service Unavailable" })).toBe(
      "Speaking the reply failed: Service Unavailable",
    );
  });

  it("says when only the first part was spoken", () => {
    expect(describeSpeakError(new Error("timeout"), { spoken: 1, total: 3 })).toBe(
      "Spoke the first part of the reply, then speaking stopped: timeout",
    );
    expect(describeSpeakError(new Error("timeout"))).toBe("Speaking the reply failed: timeout");
    expect(describeSpeakError({})).toBe("Speaking the reply failed");
  });
});
