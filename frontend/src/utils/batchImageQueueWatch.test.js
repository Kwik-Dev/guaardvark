import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import {
  batchToResume,
  createGalleryRefresher,
  diffImageQueue,
  imageProcessEvents,
  overlayLiveCounts,
  queueDiffNeedsRefresh,
} from "./batchImageQueueWatch";

const row = (batch_id, status, completed_images = 0, extra = {}) => ({
  batch_id, status, completed_images, total_images: 4, ...extra,
});

describe("diffImageQueue", () => {
  it("reports nothing on the first poll", () => {
    const diff = diffImageQueue(null, [row("A", "running", 1)]);
    expect(diff).toEqual({ finished: [], grew: [], appeared: [] });
    expect(queueDiffNeedsRefresh(diff)).toBe(false);
  });

  it("sees a batch behind the open one finish", () => {
    // B is the batch on screen; A finishes behind it and must still refresh the gallery.
    const prev = [row("A", "running", 3), row("B", "queued")];
    const next = [row("A", "completed", 4), row("B", "running")];
    const diff = diffImageQueue(prev, next);
    expect(diff.finished).toEqual(["A"]);
    expect(queueDiffNeedsRefresh(diff)).toBe(true);
  });

  it("sees images land on a running batch", () => {
    const diff = diffImageQueue([row("A", "running", 1)], [row("A", "running", 2)]);
    expect(diff).toEqual({ finished: [], grew: ["A"], appeared: [] });
  });

  it("sees a batch queued from chat appear", () => {
    const diff = diffImageQueue([], [row("C", "queued")]);
    expect(diff.appeared).toEqual(["C"]);
    expect(queueDiffNeedsRefresh(diff)).toBe(true);
  });

  it("stays quiet when nothing moved", () => {
    const rows = [row("A", "running", 2), row("B", "completed", 4)];
    expect(queueDiffNeedsRefresh(diffImageQueue(rows, rows.map((r) => ({ ...r }))))).toBe(false);
  });

  it("does not report an already finished batch twice", () => {
    const diff = diffImageQueue([row("A", "completed", 4)], [row("A", "completed", 4)]);
    expect(diff.finished).toEqual([]);
  });
});

describe("createGalleryRefresher", () => {
  let reload;
  let libraryEvents;
  let refresher;
  const onLibrary = () => { libraryEvents += 1; };

  beforeEach(() => {
    vi.useFakeTimers();
    reload = vi.fn();
    libraryEvents = 0;
    window.addEventListener("images-updated", onLibrary);
    refresher = createGalleryRefresher({
      reload,
      notifyLibrary: () => window.dispatchEvent(new Event("images-updated")),
      delayMs: 1500,
    });
  });

  afterEach(() => {
    refresher.cancel();
    window.removeEventListener("images-updated", onLibrary);
    vi.useRealTimers();
  });

  it("reloads Recent Batches as images land, without telling the library", () => {
    refresher.observeQueue([row("A", "running", 1)]);
    refresher.observeQueue([row("A", "running", 2)]);
    expect(reload).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1500);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(libraryEvents).toBe(0);
  });

  it("reloads once per window however many polls move", () => {
    refresher.observeQueue([row("A", "running", 1)]);
    refresher.observeQueue([row("A", "running", 2)]);
    refresher.observeQueue([row("A", "running", 3)]);
    refresher.observeQueue([row("A", "running", 4)]);
    vi.advanceTimersByTime(1500);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("tells the library when a batch behind the open one finishes", () => {
    refresher.observeQueue([row("A", "running", 3), row("B", "queued")]);
    refresher.observeQueue([row("A", "completed", 4), row("B", "running")]);
    vi.advanceTimersByTime(1500);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(libraryEvents).toBe(1);
  });

  it("picks up a batch queued from elsewhere and knows its id afterwards", () => {
    refresher.observeQueue([]);
    refresher.observeQueue([row("C", "queued")]);
    expect(refresher.knownBatchIds()).toEqual(new Set(["C"]));
    vi.advanceTimersByTime(1500);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("does nothing while the queue stands still, and cancel drops a pending reload", () => {
    refresher.observeQueue([row("A", "running", 1)]);
    refresher.observeQueue([row("A", "running", 1)]);
    vi.advanceTimersByTime(5000);
    expect(reload).not.toHaveBeenCalled();
    refresher.schedule({ library: true });
    refresher.cancel();
    vi.advanceTimersByTime(5000);
    expect(reload).not.toHaveBeenCalled();
    expect(libraryEvents).toBe(0);
  });
});

describe("batchToResume", () => {
  it("prefers the running batch", () => {
    expect(batchToResume([row("A", "queued"), row("B", "running")])).toBe("B");
    expect(batchToResume([row("A", "queued", 0, { is_running: true }), row("B", "queued")])).toBe("A");
  });

  it("falls back to the first waiting batch and ignores finished ones", () => {
    expect(batchToResume([row("A", "completed"), row("B", "pending"), row("C", "queued")])).toBe("B");
    expect(batchToResume([row("A", "completed"), row("B", "error")])).toBe(null);
    expect(batchToResume(undefined)).toBe(null);
  });
});

describe("overlayLiveCounts", () => {
  it("counts a running batch's card up from the queue", () => {
    const history = [row("A", "running", 0), row("B", "completed", 4)];
    const out = overlayLiveCounts(history, [row("A", "running", 3)]);
    expect(out[0]).toMatchObject({ batch_id: "A", completed_images: 3, status: "running" });
    expect(out[1]).toBe(history[1]);
  });

  it("carries a finish the history has not reloaded yet", () => {
    const out = overlayLiveCounts([row("A", "running", 3)], [row("A", "completed", 4)]);
    expect(out[0]).toMatchObject({ status: "completed", completed_images: 4 });
  });

  it("never counts down and keeps identity when nothing changes", () => {
    const history = [row("A", "running", 3)];
    expect(overlayLiveCounts(history, [row("A", "running", 2)])).toBe(history);
    expect(overlayLiveCounts(history, [])).toBe(history);
  });
});

describe("imageProcessEvents", () => {
  const proc = (batch_id, status, processType = "image_generation") => ({
    processType, status, additional_data: { batch_id },
  });

  it("reports finishes once and running batches the queue does not list", () => {
    const handled = new Set(["OLD"]);
    const known = new Set(["A"]);
    const out = imageProcessEvents(
      [proc("A", "processing"), proc("B", "complete"), proc("OLD", "end"), proc("C", "processing"),
        proc("V", "complete", "video_render")],
      handled,
      known,
    );
    expect(out).toEqual({ finished: ["B"], unknown: ["C"] });
  });

  it("reads the legacy process_type field and skips events without a batch", () => {
    const out = imageProcessEvents(
      [{ process_type: "image_generation", status: "error", additional_data: { batch_id: "E" } },
        { processType: "image_generation", status: "complete", additional_data: {} }],
      new Set(),
      new Set(),
    );
    expect(out).toEqual({ finished: ["E"], unknown: [] });
  });
});
