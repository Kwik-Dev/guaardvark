import { describe, it, expect } from "vitest";
import { applicableAdapters } from "./videoAdapters";

const ltxLora = { id: "ltx-distilled-lora", type: "lora", applies_to: [], adapter: false };
const wanStyle = { id: "user-wan-style", type: "lora", applies_to: ["wan22-5b"] };
const h3Turbo = { id: "minimax-h3-fl2v-turbo-8step", type: "lora", applies_to: ["minimax-h3-int8"] };
const unscoped = { id: "user-loose", type: "lora" };

describe("applicableAdapters", () => {
  it("never offers a LoRA that names no models", () => {
    const rows = [ltxLora, unscoped, wanStyle, h3Turbo];
    expect(applicableAdapters(rows, "wan22-5b").map((m) => m.id)).toEqual(["user-wan-style"]);
    expect(applicableAdapters(rows, "minimax-h3-int8").map((m) => m.id)).toEqual([
      "minimax-h3-fl2v-turbo-8step",
    ]);
    expect(applicableAdapters(rows, "ltx23-distilled-fp8")).toEqual([]);
  });

  it("hides a LoRA marked adapter false even if it names the model", () => {
    const rows = [{ ...wanStyle, adapter: false }];
    expect(applicableAdapters(rows, "wan22-5b")).toEqual([]);
  });

  it("leaves speed-profile LoRAs to the profile picker", () => {
    const profiles = {
      turbo: { lora: "minimax-h3-fl2v-turbo-8step" },
      lightning: { loras: { high: "a", low: "b" } },
    };
    const rows = [h3Turbo, { id: "a", applies_to: ["minimax-h3-int8"] }, { id: "c", applies_to: ["minimax-h3-int8"] }];
    expect(applicableAdapters(rows, "minimax-h3-int8", profiles).map((m) => m.id)).toEqual(["c"]);
  });

  it("tolerates missing inputs", () => {
    expect(applicableAdapters(undefined, "wan22-5b")).toEqual([]);
    expect(applicableAdapters(null, "wan22-5b", null)).toEqual([]);
  });
});
