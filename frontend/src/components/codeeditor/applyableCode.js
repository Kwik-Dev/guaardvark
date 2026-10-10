// frontend/src/components/codeeditor/applyableCode.js
// Picks the code a chat reply offers for "Apply". A model reply is prose with
// fenced code blocks in it; only the code in a block may replace the file or
// the selection. A reply without a block is shown as text and never applied.

const LANGUAGE_ALIASES = {
  js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript", javascript: "javascript",
  ts: "typescript", tsx: "typescript", typescript: "typescript",
  py: "python", python: "python", python3: "python",
  sh: "shell", bash: "shell", zsh: "shell", shell: "shell",
  md: "markdown", markdown: "markdown",
  cs: "csharp", csharp: "csharp", "c#": "csharp",
  cpp: "cpp", "c++": "cpp", cc: "cpp", cxx: "cpp",
  yml: "yaml", yaml: "yaml",
  rs: "rust", rust: "rust",
  rb: "ruby", ruby: "ruby",
  kt: "kotlin", kotlin: "kotlin",
  go: "go", golang: "go",
};

export const canonicalLanguage = (name) => {
  const key = String(name || "").trim().toLowerCase();
  if (!key) return "";
  return LANGUAGE_ALIASES[key] || key;
};

// A fence opens at the start of a line with three or more backticks or tildes,
// optionally followed by an info string, and closes with the same run at the
// start of a later line.
const FENCE = /^[ \t]*(`{3,}|~{3,})[ \t]*([^\s`]*)[^\n]*\n([\s\S]*?)^[ \t]*\1[ \t]*$/gm;

/** All fenced blocks in a reply, in order: [{ language, code }]. */
export function fencedBlocks(reply) {
  if (typeof reply !== "string" || !reply) return [];
  const text = reply.replace(/\r\n/g, "\n");
  const blocks = [];
  for (const match of text.matchAll(FENCE)) {
    const code = match[3].replace(/\n$/, "");
    if (code.trim()) blocks.push({ language: canonicalLanguage(match[2]), code });
  }
  return blocks;
}

/**
 * The code to offer for Apply, or null when the reply holds no code block.
 * With several blocks, the ones in the file's language win; among those (or
 * all, when none match) the largest is taken.
 */
export function extractApplyableCode(reply, fileLanguage) {
  const blocks = fencedBlocks(reply);
  if (blocks.length === 0) return null;
  const wanted = canonicalLanguage(fileLanguage);
  const matching = wanted ? blocks.filter((b) => b.language === wanted) : [];
  const pool = matching.length ? matching : blocks;
  return pool.reduce((best, b) => (b.code.length > best.code.length ? b : best)).code;
}
