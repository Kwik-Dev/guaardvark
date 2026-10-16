// frontend/src/components/social/oauthCode.js
// The authorization code from what a person pasted after approving access:
// the code itself, or the whole address the browser landed on. An address
// must carry the state this dialog's authorization started with, so a code
// from another attempt (or someone else's link) is not used.

/**
 * @param {string} pasted
 * @param {string} [expectedState] the state returned by oauth/start
 * @returns {{ code: string, error: string | null }}
 */
export const codeFromPaste = (pasted, expectedState) => {
  const text = (pasted || "").trim();
  if (!text) return { code: "", error: null };
  if (!/^https?:\/\//i.test(text)) {
    // Some platforms end the redirect with "#_", which a copied code keeps.
    return { code: text.split("#")[0], error: null };
  }
  let url;
  try {
    url = new URL(text);
  } catch {
    return { code: "", error: "That address could not be read. Paste it again, or just the code." };
  }
  const params = url.searchParams;
  const denied = params.get("error");
  if (denied) {
    const reason = params.get("error_description") || denied;
    return { code: "", error: `Access was not granted: ${reason}. Authorize again.` };
  }
  const code = params.get("code");
  if (!code) return { code: "", error: "That address has no authorization code in it." };
  const state = params.get("state");
  if (expectedState && state !== expectedState) {
    return {
      code: "",
      error: "That address is from a different authorization attempt. Press Authorize again and use the new one.",
    };
  }
  return { code, error: null };
};
