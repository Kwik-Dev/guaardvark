// frontend/src/utils/copyText.js

/**
 * Copy text to the clipboard. navigator.clipboard only exists on secure origins,
 * so a box reached over plain http on the LAN falls back to execCommand.
 * Resolves true when the text was copied.
 */
export default async function copyText(text) {
  const value = text == null ? "" : String(text);
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = value;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}
