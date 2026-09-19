/**
 * Consent-variant fields on chat:tool_approval_request.
 *
 * Contract the identity-routing work writes to (and this card reads):
 *   {
 *     tools, tool_details, iteration, available_scopes,
 *     consent: true,
 *     image?: path | URL,          // also image_url / reference_image / reference_image_url
 *     prompt?: string,             // also scene_prompt
 *   }
 * tool_details[] may repeat those keys or nest them under params.
 * params.consent / params.consented are treated the same as payload.consent.
 * Approve still emits chat:tool_approval_response { session_id, approved }.
 */

const TRUTHY = new Set([true, "true", "True", 1, "1", "yes", "YES"]);

export function truthyFlag(value) {
  return TRUTHY.has(value);
}

function firstString(...candidates) {
  for (const value of candidates) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

export function parseConsentApproval(data, toolName, existingParams = {}) {
  const details = Array.isArray(data?.tool_details)
    ? data.tool_details.find((d) => d && d.tool === toolName) || {}
    : {};
  const params = {
    ...(existingParams || {}),
    ...(details.params || {}),
  };
  const consent =
    truthyFlag(data?.consent) ||
    truthyFlag(details.consent) ||
    truthyFlag(params.consent) ||
    truthyFlag(params.consented);
  const image = firstString(
    data?.image,
    data?.image_url,
    data?.reference_image,
    data?.reference_image_url,
    details.image,
    details.image_url,
    details.reference_image,
    params.image,
    params.image_url,
  );
  const prompt = firstString(
    data?.prompt,
    data?.scene_prompt,
    details.prompt,
    details.scene_prompt,
    params.prompt,
  );
  return { consent, image, prompt, params };
}

function looksLikeFilesystemPath(raw) {
  if (/^[A-Za-z]:[\\/]/.test(raw)) return true;
  return /^\/(home|tmp|var|opt|usr|data|Users|mnt|root|private|Volumes)\b/.test(raw);
}

/**
 * Turn a reference path/URL into something <img> can load. Raw filesystem
 * paths that are not under a served outputs/uploads folder return null.
 */
export function consentImageSrc(value) {
  if (!value || typeof value !== "string") return null;
  const raw = value.trim();
  if (!raw) return null;
  if (raw.startsWith("data:") || raw.startsWith("blob:")) return raw;
  if (/^https?:\/\//i.test(raw)) return raw;
  if (raw.startsWith("file://")) {
    try {
      return consentImageSrc(decodeURIComponent(raw.slice("file://".length)));
    } catch {
      return null;
    }
  }
  const generated = raw.match(/(?:^|[\\/])generated_images[\\/]([^\\/]+)$/);
  if (generated) return `/api/outputs/generated_images/${generated[1]}`;
  const uploads = raw.match(/(?:^|[\\/])uploads[\\/]([^\\/]+)$/);
  if (uploads) return `/api/uploads/${uploads[1]}`;
  if (raw.startsWith("/") && !looksLikeFilesystemPath(raw)) return raw;
  return null;
}
