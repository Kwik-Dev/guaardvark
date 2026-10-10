import { describe, it, expect } from "vitest";
import { describeGpuHolder } from "../useJobsGate";

// The banner says what is using the card in plain words; the job's kind and
// id go to a tooltip at most.
describe("describeGpuHolder", () => {
  it("names an editor render without its id", () => {
    const { reason, detail } = describeGpuHolder({ kind: "video_render", native_id: "editor_42" });
    expect(reason).toBe("A video is rendering");
    expect(reason).not.toContain("42");
    expect(detail).toBe("video_render:editor_42");
  });

  it("does not call a picture or a song a video: video_render is the general kind", () => {
    expect(describeGpuHolder({ kind: "video_render", native_id: "stills_chat_ab12cd34" }).reason)
      .toBe("A picture is rendering");
    expect(describeGpuHolder({ kind: "video_render", native_id: "gen_ab12cd34" }).reason)
      .toBe("A picture is rendering");
    expect(describeGpuHolder({ kind: "video_render", native_id: "video:music3:9" }).reason)
      .toBe("A song is rendering");
    expect(describeGpuHolder({ kind: "video_render", native_id: "42" }).reason)
      .toBe("Something is using the graphics card");
  });

  it("reads the kind off a batch id prefix", () => {
    expect(describeGpuHolder({ kind: "task", native_id: "ImageBatch_04-02-2026_001" }).reason)
      .toBe("Pictures are rendering");
    expect(describeGpuHolder({ kind: "task", native_id: "VideoBatch_04-02-2026_001" }).reason)
      .toBe("A video is rendering");
  });

  it("falls back to a plain sentence for an unknown kind", () => {
    const { reason, detail } = describeGpuHolder({ kind: "experiment", native_id: "x1" });
    expect(reason).toBe("Something is using the graphics card");
    expect(detail).toBe("experiment:x1");
  });

  it("is empty without a holder", () => {
    expect(describeGpuHolder(null)).toEqual({ reason: null, detail: null });
  });
});
