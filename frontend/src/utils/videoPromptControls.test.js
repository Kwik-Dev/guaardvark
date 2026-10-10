import { describe, it, expect } from "vitest";
import { promptControlsState } from "./videoPromptControls";

const base = { enhancePrompt: true, verbatimPrompts: false, inputMode: "text" };

describe("promptControlsState: Motion chips", () => {
  it("reach the enhancer with Enhance on and Verbatim off", () => {
    const { motion } = promptControlsState(base);
    expect(motion).toEqual({ active: true, note: "via prompt enhancer" });
  });

  it("do nothing with Enhance off, and say so", () => {
    const { motion } = promptControlsState({ ...base, enhancePrompt: false });
    expect(motion.active).toBe(false);
    expect(motion.note).toMatch(/Enhance prompt is off/);
  });

  it("do nothing under Verbatim prompts on Text and Image input, naming Settings", () => {
    for (const inputMode of ["text", "image"]) {
      const { motion } = promptControlsState({ ...base, inputMode, verbatimPrompts: true });
      expect(motion.active).toBe(false);
      expect(motion.note).toMatch(/Verbatim prompts is on in Settings/);
    }
  });

  it("still reach the reference compiler under Verbatim on References input", () => {
    const { motion } = promptControlsState({ ...base, inputMode: "references", verbatimPrompts: true });
    expect(motion).toEqual({ active: true, note: "via the reference compiler" });
  });
});

describe("promptControlsState: Prompt style", () => {
  it("is read by the enhancer in the plain case", () => {
    expect(promptControlsState(base).style).toEqual({ active: true, note: "" });
  });

  it("is dropped under Verbatim prompts", () => {
    const { style } = promptControlsState({ ...base, verbatimPrompts: true });
    expect(style.active).toBe(false);
    expect(style.note).toMatch(/Verbatim prompts is on in Settings/);
  });

  it("is dropped with Enhance off unless the Director or Storyboard reads it", () => {
    expect(promptControlsState({ ...base, enhancePrompt: false }).style.active).toBe(false);
    expect(promptControlsState({ ...base, enhancePrompt: false, directorMode: true }).style.active).toBe(true);
    expect(promptControlsState({ ...base, enhancePrompt: false, storyboardMode: true }).style.active).toBe(true);
  });

  it("survives Verbatim only through Storyboard, which the worker keeps", () => {
    expect(promptControlsState({ ...base, verbatimPrompts: true, directorMode: true }).style.active).toBe(false);
    expect(promptControlsState({ ...base, verbatimPrompts: true, storyboardMode: true }).style.active).toBe(true);
    expect(promptControlsState({ ...base, inputMode: "image", verbatimPrompts: true, storyboardMode: true }).style.active).toBe(false);
  });

  it("is dropped by the reference compiler under Exact text", () => {
    const { style } = promptControlsState({ ...base, inputMode: "references", fidelityMode: true });
    expect(style.active).toBe(false);
    expect(style.note).toMatch(/Exact text/);
  });
});
