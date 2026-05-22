import { describe, it, expect } from "vitest";
import { normalizeTimeline } from "./normalizeTimeline";

describe("normalizeTimeline", () => {
  it("returns an empty timeline for null/garbage input", () => {
    expect(normalizeTimeline(null)).toEqual({ bin: [], textElements: [] });
    expect(normalizeTimeline(undefined)).toEqual({ bin: [], textElements: [] });
  });

  it("defaults legacy bin items (no kind) to video", () => {
    const out = normalizeTimeline({ bin: [{ clipId: "doc1", documentId: 1, filename: "a.mp4" }] });
    expect(out.bin[0].kind).toBe("video");
  });

  it("migrates a legacy song slot into a flagged audio bin item", () => {
    const out = normalizeTimeline({
      bin: [{ clipId: "doc1", documentId: 1, filename: "a.mp4" }],
      song: { documentId: 9, filename: "track.wav", volume: 0.8 },
    });
    expect(out.song).toBeUndefined();
    const song = out.bin.find((c) => c.documentId === 9);
    expect(song).toBeTruthy();
    expect(song.kind).toBe("audio");
    expect(song.isMasterSong).toBe(true);
    expect(song.volume).toBe(0.8);
  });

  it("flags an existing bin item when the legacy song already in the bin", () => {
    const out = normalizeTimeline({
      bin: [{ clipId: "doc9", documentId: 9, filename: "track.wav" }],
      song: { documentId: 9, filename: "track.wav", volume: 0.5 },
    });
    expect(out.bin).toHaveLength(1);
    expect(out.bin[0].kind).toBe("audio");
    expect(out.bin[0].isMasterSong).toBe(true);
    expect(out.bin[0].volume).toBe(0.5);
  });

  it("leaves a modern timeline (kinds, no song) untouched in shape", () => {
    const out = normalizeTimeline({
      bin: [
        { clipId: "doc1", documentId: 1, filename: "a.mp4", kind: "video" },
        { clipId: "doc2", documentId: 2, filename: "b.wav", kind: "audio", isMasterSong: true, volume: 1 },
      ],
      textElements: [],
    });
    expect(out.bin).toHaveLength(2);
    expect(out.bin[1].isMasterSong).toBe(true);
  });

  it("enforces the single-master-song invariant", () => {
    const out = normalizeTimeline({
      bin: [
        { clipId: "doc1", documentId: 1, filename: "a.wav", kind: "audio", isMasterSong: true },
        { clipId: "doc2", documentId: 2, filename: "b.wav", kind: "audio", isMasterSong: true },
      ],
    });
    const flagged = out.bin.filter((c) => c.isMasterSong);
    expect(flagged).toHaveLength(1);
  });
});
