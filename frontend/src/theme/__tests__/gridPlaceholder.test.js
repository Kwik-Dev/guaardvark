// @vitest-environment jsdom
import { describe, it, expect, afterEach } from "vitest";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { themes } from "../themes";
import { themeCssVars } from "../cssVars";

// Vitest runs from frontend/.
const indexCss = readFileSync(path.join(process.cwd(), "src/index.css"), "utf8");
const libraryCss = readFileSync(
  createRequire(path.join(process.cwd(), "package.json")).resolve("react-grid-layout/css/styles.css"),
  "utf8",
);

afterEach(() => {
  document.head.innerHTML = "";
  document.body.innerHTML = "";
});

describe("grid drop placeholder", () => {
  it("publishes every theme's primary colour for index.css", () => {
    for (const { theme } of Object.values(themes)) {
      expect(themeCssVars(theme.palette)["--primary-main"]).toBe(theme.palette.primary.main);
    }
  });

  it("draws a dashed outline instead of the library's red block, whichever sheet loads last", () => {
    for (const sheets of [[libraryCss, indexCss], [indexCss, libraryCss]]) {
      document.head.innerHTML = sheets.map((css) => `<style>${css}</style>`).join("");
      document.body.innerHTML =
        '<div class="react-grid-layout"><div class="react-grid-item react-grid-placeholder" id="ph"></div></div>';
      const style = getComputedStyle(document.getElementById("ph"));
      expect(style.backgroundColor).not.toBe("red");
      expect(style.borderTopStyle).toBe("dashed");
    }
    expect(indexCss).toMatch(/react-grid-placeholder\s*\{[^}]*border-color:\s*var\(--primary-main/);
  });
});
