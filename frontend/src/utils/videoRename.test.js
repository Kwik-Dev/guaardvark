import { describe, it, expect } from "vitest";
import { applyClipRename, applyClipRenameToPlayer, clipFileName } from "./videoRename";

const renamed = {
  old_video_path: "item_1/videos/clip_a.mp4",
  video_path: "item_1/videos/sunset.mp4",
  thumbnail_path: "item_1/thumbnails/sunset_thumb.jpg",
};

const status = () => ({
  batch_id: "B1",
  status: "completed",
  results: [
    {
      item_id: "item_1",
      video_path: "item_1/videos/clip_a.mp4",
      thumbnail_path: "item_1/thumbnails/clip_a_thumb.jpg",
      frame_paths: ["item_1/videos/clip_a.mp4"],
    },
    { item_id: "item_2", video_path: "item_2/videos/clip_b.mp4", frame_paths: [] },
  ],
});

describe("clipFileName", () => {
  it("shows only the file name in the prompt", () => {
    expect(clipFileName("item_1/videos/clip_a.mp4")).toBe("clip_a.mp4");
    expect(clipFileName("clip.mp4")).toBe("clip.mp4");
    expect(clipFileName("")).toBe("");
    expect(clipFileName(null)).toBe("");
  });
});

describe("applyClipRename", () => {
  it("repoints the renamed result's video, thumbnail and frames", () => {
    const before = status();
    const after = applyClipRename(before, "B1", renamed);
    expect(after).not.toBe(before);
    expect(after.results[0]).toMatchObject({
      video_path: "item_1/videos/sunset.mp4",
      thumbnail_path: "item_1/thumbnails/sunset_thumb.jpg",
      frame_paths: ["item_1/videos/sunset.mp4"],
    });
    expect(after.results[1]).toBe(before.results[1]);
    expect(before.results[0].video_path).toBe("item_1/videos/clip_a.mp4");
  });

  it("keeps the thumbnail when the server did not move one", () => {
    const after = applyClipRename(status(), "B1", { ...renamed, thumbnail_path: null });
    expect(after.results[0].thumbnail_path).toBe("item_1/thumbnails/clip_a_thumb.jpg");
  });

  it("leaves another batch and a non-matching rename alone", () => {
    const before = status();
    expect(applyClipRename(before, "B2", renamed)).toBe(before);
    expect(applyClipRename(before, "B1", { ...renamed, old_video_path: "x/y.mp4" })).toBe(before);
    expect(applyClipRename(null, "B1", renamed)).toBe(null);
  });

  it("matches a leading-slash path the way PathFromUrl strips it", () => {
    const before = { batch_id: "B1", results: [{ item_id: "i", video_path: "/item_1/videos/clip_a.mp4" }] };
    expect(applyClipRename(before, "B1", renamed).results[0].video_path).toBe("item_1/videos/sunset.mp4");
  });
});

describe("applyClipRenameToPlayer", () => {
  const urlFor = (p) => `/api/batch-video/video/B1/${p}`;
  const player = (currentIndex) => ({
    url: urlFor(status().results[currentIndex].video_path),
    title: "clip",
    batchId: "B1",
    results: status().results,
    currentIndex,
  });

  it("moves the url and title when the playing clip was renamed", () => {
    const after = applyClipRenameToPlayer(player(0), "B1", renamed, urlFor);
    expect(after.url).toBe("/api/batch-video/video/B1/item_1/videos/sunset.mp4");
    expect(after.title).toBe("sunset.mp4");
    expect(after.results[0].video_path).toBe("item_1/videos/sunset.mp4");
  });

  it("only repoints the playlist when another clip was renamed", () => {
    const before = player(1);
    const after = applyClipRenameToPlayer(before, "B1", renamed, urlFor);
    expect(after.url).toBe(before.url);
    expect(after.results[0].video_path).toBe("item_1/videos/sunset.mp4");
  });

  it("ignores a closed player and another batch", () => {
    expect(applyClipRenameToPlayer(null, "B1", renamed, urlFor)).toBe(null);
    const other = player(0);
    expect(applyClipRenameToPlayer(other, "B9", renamed, urlFor)).toBe(other);
  });
});
