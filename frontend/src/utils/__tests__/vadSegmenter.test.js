import { describe, it, expect } from "vitest";
import { createVadSegmenter } from "../vadSegmenter";

const CONFIG = {
  threshold: 0.03,
  hysteresis: 0.6,
  startConfirmMs: 60,
  silenceMs: 1500,
  minSpeechMs: 300,
  maxSegmentMs: 30000,
};

/** Feed `ms` of frames at `level`, 30 ms apart, collecting events. */
function feed(vad, state, level, ms) {
  const events = [];
  const end = state.t + ms;
  for (; state.t < end; state.t += 30) {
    const ev = vad.push(level, state.t);
    if (ev) events.push({ ...ev, t: state.t });
  }
  return events;
}

describe("createVadSegmenter", () => {
  it("starts after the confirmation time and ends after the silence time", () => {
    const vad = createVadSegmenter(CONFIG);
    const state = { t: 0 };
    expect(feed(vad, state, 0.001, 300)).toEqual([]);
    const start = feed(vad, state, 0.08, 1000);
    expect(start).toHaveLength(1);
    expect(start[0].type).toBe("speech-start");
    const end = feed(vad, state, 0.001, 2000);
    expect(end).toHaveLength(1);
    expect(end[0]).toMatchObject({ type: "speech-end", reason: "silence" });
    expect(end[0].speechMs).toBeGreaterThanOrEqual(900);
    expect(vad.speaking).toBe(false);
  });

  it("ignores a click shorter than the confirmation time", () => {
    const vad = createVadSegmenter(CONFIG);
    const state = { t: 0 };
    expect(feed(vad, state, 0.5, 30)).toEqual([]);
    expect(feed(vad, state, 0.001, 2000)).toEqual([]);
  });

  it("keeps an utterance through dips above the hysteresis level", () => {
    const vad = createVadSegmenter(CONFIG);
    const state = { t: 0 };
    feed(vad, state, 0.08, 500);
    // 0.02 is under the start threshold but over 0.03 * 0.6.
    expect(feed(vad, state, 0.02, 3000)).toEqual([]);
    expect(vad.speaking).toBe(true);
  });

  it("discards speech shorter than the minimum", () => {
    const vad = createVadSegmenter(CONFIG);
    const state = { t: 0 };
    feed(vad, state, 0.08, 150);
    const events = feed(vad, state, 0.001, 2000);
    expect(events).toEqual([expect.objectContaining({ type: "discard" })]);
  });

  it("cuts an utterance at the maximum length and starts the next one", () => {
    const vad = createVadSegmenter({ ...CONFIG, maxSegmentMs: 2000 });
    const state = { t: 0 };
    const events = feed(vad, state, 0.08, 4500).map((e) => e.type);
    expect(events.slice(0, 3)).toEqual(["speech-start", "speech-end", "speech-start"]);
  });

  it("uses a new threshold from setConfig without losing an utterance", () => {
    const vad = createVadSegmenter(CONFIG);
    const state = { t: 0 };
    feed(vad, state, 0.08, 300);
    vad.setConfig({ threshold: 0.1 });
    expect(vad.speaking).toBe(true);
    expect(feed(vad, state, 0.08, 300)).toEqual([]);
  });
});
