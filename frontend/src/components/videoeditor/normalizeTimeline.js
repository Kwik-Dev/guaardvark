// Normalizes a (possibly legacy) persisted timeline into the current shape:
//   { bin: [{clipId, documentId, filename, kind, keptRanges, durationSeconds,
//             isMasterSong?, volume?}], textElements: [] }
// Two migrations from the old shape:
//   - bin items had no `kind` → default "video" (the legacy bin was video-only).
//   - a separate `song` slot → the master soundtrack is now a flagged audio bin
//     item (isMasterSong + volume), so an old `song` becomes one.
export function normalizeTimeline(t) {
  if (!t || typeof t !== "object") return { bin: [], textElements: [] };

  const bin = (Array.isArray(t.bin) ? t.bin : []).map((c) => ({
    ...c,
    kind: c.kind || "video",
  }));

  if (t.song && t.song.documentId != null) {
    const existing = bin.find((c) => c.documentId === t.song.documentId);
    if (existing) {
      existing.kind = "audio";
      existing.isMasterSong = true;
      existing.volume = t.song.volume ?? 1.0;
    } else {
      bin.push({
        clipId: `doc${t.song.documentId}`,
        documentId: t.song.documentId,
        filename: t.song.filename || "(song)",
        kind: "audio",
        isMasterSong: true,
        volume: t.song.volume ?? 1.0,
        keptRanges: null,
        durationSeconds: null,
      });
    }
  }

  // Single-flag invariant: at most one master song.
  let seenMaster = false;
  for (const c of bin) {
    if (c.isMasterSong) {
      if (seenMaster) c.isMasterSong = false;
      else seenMaster = true;
    }
  }

  return { bin, textElements: Array.isArray(t.textElements) ? t.textElements : [] };
}
