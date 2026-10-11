import { describe, it, expect } from "vitest";
import { extractApplyableCode, fencedBlocks, canonicalLanguage } from "../applyableCode";

const reply = [
  "Here is the change you asked for.",
  "",
  "```python",
  "def add(a, b):",
  "    return a + b",
  "```",
  "",
  "Notes: the function now returns the sum.",
].join("\n");

describe("extractApplyableCode", () => {
  it("returns only the code inside the fence, without the prose around it", () => {
    expect(extractApplyableCode(reply, "python")).toBe("def add(a, b):\n    return a + b");
  });

  it("returns null for a reply with no code block, so nothing is offered for Apply", () => {
    expect(extractApplyableCode("I would rename the variable and add a docstring.", "python")).toBe(null);
    expect(extractApplyableCode("", "python")).toBe(null);
    expect(extractApplyableCode(undefined, "python")).toBe(null);
  });

  it("prefers a block in the file's language over a larger one in another", () => {
    const mixed = [
      "```bash",
      "pip install requests",
      "pip install flask",
      "pip install pytest",
      "```",
      "```py",
      "import requests",
      "```",
    ].join("\n");
    expect(extractApplyableCode(mixed, "python")).toBe("import requests");
    expect(extractApplyableCode(mixed, "shell")).toBe("pip install requests\npip install flask\npip install pytest");
  });

  it("takes the largest block when none is in the file's language", () => {
    const mixed = "```\nshort\n```\n\n```js\nconst a = 1;\nconst b = 2;\n```\n";
    expect(extractApplyableCode(mixed, "python")).toBe("const a = 1;\nconst b = 2;");
  });

  it("handles CRLF replies, tilde fences and indented closing fences", () => {
    expect(extractApplyableCode("```js\r\nlet x = 1;\r\n```\r\n", "javascript")).toBe("let x = 1;");
    expect(extractApplyableCode("~~~python\nx = 1\n~~~", "python")).toBe("x = 1");
    expect(extractApplyableCode("```python\nx = 1\n  ```", "python")).toBe("x = 1");
  });

  it("skips empty blocks and keeps backticks inside code", () => {
    expect(fencedBlocks("```js\n\n```")).toEqual([]);
    expect(extractApplyableCode("```js\nconst s = `a${b}`;\n```", "javascript")).toBe("const s = `a${b}`;");
  });

  it("maps fence info strings and Monaco language ids to one name", () => {
    expect(canonicalLanguage("py")).toBe("python");
    expect(canonicalLanguage("JSX")).toBe("javascript");
    expect(canonicalLanguage("TypeScript")).toBe("typescript");
    expect(canonicalLanguage("")).toBe("");
  });
});
