// Convert a CSS colour to the "#rrggbb" form an <input type="color"> accepts.
// Alpha is dropped: the native picker has no alpha channel.

const DEFAULT_HEX = "#1e1e1e";

const clampByte = (n) => Math.min(255, Math.max(0, Math.round(n)));

const toByte = (part) => {
  const value = part.trim();
  if (value.endsWith("%")) return clampByte((parseFloat(value) / 100) * 255);
  return clampByte(parseFloat(value));
};

const bytesToHex = (r, g, b) =>
  "#" + [r, g, b].map((n) => n.toString(16).padStart(2, "0")).join("");

// Let the browser resolve anything else (named colours, hsl(), ...). A 2D
// canvas context normalises an opaque fillStyle to "#rrggbb". Returns null
// when there is no canvas (jsdom) or the colour is not one CSS recognises.
const resolveWithCanvas = (color) => {
  try {
    const ctx = document.createElement("canvas").getContext("2d");
    if (!ctx) return null;
    const probe = "#010203";
    ctx.fillStyle = probe;
    ctx.fillStyle = color;
    const resolved = String(ctx.fillStyle).toLowerCase();
    if (resolved === probe && color.toLowerCase() !== probe) return null;
    if (/^#[0-9a-f]{6}$/.test(resolved)) return resolved;
    const m = resolved.match(/^rgba?\(([^)]+)\)$/);
    if (m) {
      const [r, g, b] = m[1].split(",").map(toByte);
      return bytesToHex(r, g, b);
    }
    return null;
  } catch {
    return null;
  }
};

/**
 * @param {string} color  "#rgb", "#rrggbb", "#rrggbbaa", "rgb()", "rgba()",
 *                        a named colour, or anything else CSS accepts.
 * @param {string} fallback  returned when the colour cannot be read.
 * @returns {string} lowercase "#rrggbb"
 */
export const toHexColor = (color, fallback = DEFAULT_HEX) => {
  if (typeof color !== "string") return fallback;
  const value = color.trim();
  if (!value) return fallback;

  const hex = value.match(/^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i);
  if (hex) {
    let digits = hex[1].toLowerCase();
    if (digits.length <= 4) digits = digits.split("").map((d) => d + d).join("");
    return "#" + digits.slice(0, 6);
  }

  const rgb = value.match(/^rgba?\(\s*([^)]+)\)$/i);
  if (rgb) {
    const parts = rgb[1].split(/[\s,/]+/).filter(Boolean);
    if (parts.length >= 3) {
      const [r, g, b] = parts.slice(0, 3).map(toByte);
      if ([r, g, b].every(Number.isFinite)) return bytesToHex(r, g, b);
    }
    return fallback;
  }

  return resolveWithCanvas(value) || fallback;
};

export default toHexColor;
