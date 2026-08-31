import { describe, it, expect } from "vitest";
import { hfUrlClientError, formatWeightSize, selectedBytes } from "./hfUrl";

describe("hfUrlClientError", () => {
  it("asks for a paste when empty", () => {
    expect(hfUrlClientError("")).toMatch(/Paste a Hugging Face/);
    expect(hfUrlClientError("   ")).toMatch(/Paste a Hugging Face/);
  });

  it("rejects a non-HF host", () => {
    expect(hfUrlClientError("https://civitai.com/models/1")).toMatch(/Only Hugging Face/);
  });

  it("rejects datasets and Spaces with the same copy as the server", () => {
    expect(hfUrlClientError("https://huggingface.co/datasets/org/repo")).toMatch(/dataset or Space/);
    expect(hfUrlClientError("https://hf.co/spaces/org/app")).toMatch(/dataset or Space/);
    expect(hfUrlClientError("datasets/org/repo")).toMatch(/dataset or Space/);
  });

  it("accepts huggingface.co, hf.co, and a bare org/repo", () => {
    expect(hfUrlClientError("https://huggingface.co/org/repo")).toBe("");
    expect(hfUrlClientError("https://hf.co/org/repo/blob/main/a.safetensors")).toBe("");
    expect(hfUrlClientError("org/repo")).toBe("");
  });
});

describe("formatWeightSize", () => {
  it("formats gigabytes and skips empty", () => {
    expect(formatWeightSize(0)).toBe("");
    expect(formatWeightSize(2 * 1024 ** 3)).toBe(" (2.00 GB)");
  });
});

describe("selectedBytes", () => {
  it("sums the selected files", () => {
    const files = [
      { src: "a.safetensors", size: 10 },
      { src: "b.safetensors", size: 20 },
    ];
    expect(selectedBytes(files, ["b.safetensors"])).toBe(20);
    expect(selectedBytes(files, ["a.safetensors", "b.safetensors"])).toBe(30);
  });
});
