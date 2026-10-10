import { describe, it, expect, vi, afterEach } from "vitest";
import { toHexColor } from "./colorHex";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("toHexColor", () => {
  it("reads the Notes palette rgba swatches", () => {
    expect(toHexColor("rgba(0, 128, 128, 0.15)")).toBe("#008080");
    expect(toHexColor("rgba(30, 30, 30, 0.95)")).toBe("#1e1e1e");
    expect(toHexColor("rgba(255, 255, 255, 0.08)")).toBe("#ffffff");
    expect(toHexColor("rgb(206,147,216)")).toBe("#ce93d8");
  });

  it("accepts percentage and space-separated rgb()", () => {
    expect(toHexColor("rgb(100%, 0%, 50%)")).toBe("#ff0080");
    expect(toHexColor("rgb(0 229 255 / 0.12)")).toBe("#00e5ff");
  });

  it("normalises hex forms to lowercase #rrggbb", () => {
    expect(toHexColor("#1976D2")).toBe("#1976d2");
    expect(toHexColor("#abc")).toBe("#aabbcc");
    expect(toHexColor("#abcd")).toBe("#aabbcc");
    expect(toHexColor("#11223344")).toBe("#112233");
  });

  it("falls back when the colour cannot be read", () => {
    expect(toHexColor("")).toBe("#1e1e1e");
    expect(toHexColor(null)).toBe("#1e1e1e");
    expect(toHexColor("rgba(junk)")).toBe("#1e1e1e");
    expect(toHexColor("not-a-colour", "#abcdef")).toBe("#abcdef");
  });

  it("resolves named colours through the canvas when one exists", () => {
    const ctx = { _fill: "#000000" };
    Object.defineProperty(ctx, "fillStyle", {
      get() {
        return this._fill;
      },
      set(v) {
        if (v === "teal") this._fill = "#008080";
        else if (/^#[0-9a-f]{6}$/i.test(v)) this._fill = v.toLowerCase();
      },
    });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);

    expect(toHexColor("teal")).toBe("#008080");
    expect(toHexColor("no-such-colour")).toBe("#1e1e1e");
  });
});
