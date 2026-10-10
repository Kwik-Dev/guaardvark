import { describe, it, expect } from "vitest";
import { stepsFromProfile, stepsTileNote } from "./videoSteps";

const turbo = { label: "Turbo (8 steps)", steps: 8, min_steps: 8 };
const standard = { label: "Standard (20 steps)", steps: 20, min_steps: 20 };

describe("stepsFromProfile", () => {
  it("fixes the count when the active profile declares its steps", () => {
    expect(stepsFromProfile({ profileSpec: turbo })).toEqual({ fixed: true, steps: 8, label: "Turbo (8 steps)" });
    expect(stepsFromProfile({ profileSpec: standard }).steps).toBe(20);
  });

  it("adds the count to a label that does not carry it", () => {
    expect(stepsFromProfile({ profileSpec: { label: "Lightning", steps: 4 } }).label).toBe("Lightning (4 steps)");
    expect(stepsFromProfile({ profileSpec: { steps: 4 } }).label).toBe("speed profile (4 steps)");
  });

  it("leaves the chips live on a model without profiles", () => {
    expect(stepsFromProfile({ profileSpec: null }).fixed).toBe(false);
    expect(stepsFromProfile({}).fixed).toBe(false);
    expect(stepsFromProfile({ profileSpec: { label: "x" } }).fixed).toBe(false);
  });

  it("yields to a step count typed in Advanced", () => {
    expect(stepsFromProfile({ profileSpec: turbo, stepsTyped: true }).fixed).toBe(false);
  });
});

describe("stepsTileNote", () => {
  const fixed = stepsFromProfile({ profileSpec: turbo });
  const free = stepsFromProfile({ profileSpec: null });

  it("names the profile when it sets the steps", () => {
    expect(stepsTileNote({ lowVramClamp: false, profile: fixed, presetLabel: "Fast" })).toBe("set by Turbo (8 steps)");
  });

  it("names the quality chip otherwise", () => {
    expect(stepsTileNote({ lowVramClamp: false, profile: free, presetLabel: "Fast" })).toBe("Fast");
    expect(stepsTileNote({ lowVramClamp: false, profile: free })).toBe("preset");
  });

  it("says Low VRAM clamp only when the clamp applies", () => {
    expect(stepsTileNote({ lowVramClamp: true, profile: free, presetLabel: "Fast" })).toBe("Low VRAM clamp");
    expect(stepsTileNote({ lowVramClamp: false, profile: free, presetLabel: "Fast" })).not.toBe("Low VRAM clamp");
  });
});
