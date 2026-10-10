import { describe, it, expect } from "vitest";
import { PREVIEW_LINE_CAP, joinPreviews, previewLabel, promptsToPreview } from "./videoPreviewPrompts";

describe("promptsToPreview", () => {
  it("sends every non-empty line on Text input, Look & Feel appended as Add to queue does", () => {
    const out = promptsToPreview({ inputMode: "text", promptsText: "a cat\n\n  a dog ", lookAndFeel: " moody " });
    expect(out).toEqual({ prompts: ["a cat, moody", "a dog, moody"], total: 2, capped: false });
  });

  it("sends the lines as typed without Look & Feel", () => {
    expect(promptsToPreview({ inputMode: "text", promptsText: "a cat\na dog" }).prompts).toEqual(["a cat", "a dog"]);
  });

  it("caps a long batch and says so", () => {
    const text = Array.from({ length: PREVIEW_LINE_CAP + 5 }, (_, i) => `clip ${i}`).join("\n");
    const out = promptsToPreview({ inputMode: "text", promptsText: text });
    expect(out.prompts).toHaveLength(PREVIEW_LINE_CAP);
    expect(out.total).toBe(PREVIEW_LINE_CAP + 5);
    expect(out.capped).toBe(true);
  });

  it("sends the one description on Image and References input, and the concept in Storyboard mode", () => {
    expect(promptsToPreview({ inputMode: "image", promptsText: "slow zoom\nsecond line", lookAndFeel: "warm" }))
      .toEqual({ prompts: ["slow zoom\nsecond line, warm"], total: 1, capped: false });
    expect(promptsToPreview({ inputMode: "references", promptsText: "Maya waves" }).prompts).toEqual(["Maya waves"]);
    expect(promptsToPreview({ inputMode: "text", promptsText: "one concept\nmore", storyboardMode: true }).prompts)
      .toEqual(["one concept\nmore"]);
  });

  it("sends nothing for an empty box", () => {
    expect(promptsToPreview({ inputMode: "image", promptsText: "  " })).toEqual({ prompts: [], total: 0, capped: false });
  });
});

describe("previewLabel", () => {
  it("keeps the single-prompt wording", () => {
    expect(previewLabel({ shown: 1, total: 1 })).toBe("Enhanced prompt (what will be sent)");
    expect(previewLabel({ shown: 1, total: 1, verbatim: true })).toMatch(/^Prompt as typed \(Verbatim/);
    expect(previewLabel({ shown: 1, total: 1, enhanceOff: true })).toMatch(/^Prompt as typed \(Enhance/);
  });

  it("counts the clips of a batch", () => {
    expect(previewLabel({ shown: 3, total: 3 })).toBe("Enhanced prompts (what will be sent): 3 clips, one each");
    expect(previewLabel({ shown: 20, total: 25 })).toBe("Enhanced prompts (what will be sent): first 20 of 25 clips");
    expect(previewLabel({ shown: 2, total: 2, verbatim: true })).toMatch(/^Prompts as typed .*: 2 clips, one each$/);
  });

  it("says the Storyboard step rewrites the concept", () => {
    expect(previewLabel({ shown: 1, total: 1, storyboard: true })).toMatch(/Storyboard step writes the shots/);
  });
});

describe("joinPreviews", () => {
  it("numbers several, leaves one alone", () => {
    expect(joinPreviews(["a"])).toBe("a");
    expect(joinPreviews([])).toBe("");
    expect(joinPreviews(["a", "b"])).toBe("1. a\n\n2. b");
  });
});
