// frontend/src/components/social/platformRules.js
// Plain-words readings of a provider's declared capabilities (backend
// services/connections/base.py), shared by Social Accounts, Social Posts and
// the publish composer. Nothing here knows a platform by name.

export const describeWindow = (seconds) => {
  if (!seconds) return "";
  if (seconds % 86400 === 0) return seconds === 86400 ? "day" : `${seconds / 86400} days`;
  if (seconds % 3600 === 0) return seconds === 3600 ? "hour" : `${seconds / 3600} hours`;
  return `${Math.max(1, Math.round(seconds / 60))} minutes`;
};

export const describeRateLimit = (limit) =>
  `At most ${limit.posts} posts per ${describeWindow(limit.window_seconds)} from one account.` +
  (limit.platform_limit ? ` The platform allows ${limit.platform_limit}.` : "");

export const describeDisclosure = (rule) => {
  if (!rule) return "";
  const how =
    rule.mechanism === "platform_flag"
      ? `It is set as the platform's own flag (${rule.flag}).`
      : `It is a line at the end of the post: "${rule.text}"`;
  if (rule.required_when === "generated_media") {
    return `Required when the post carries media Guaardvark generated. ${rule.rule} ${how}`.trim();
  }
  if (rule.required_when === "ai_assisted") {
    return `Required when the post carries generated media or text an agent drafted. ${rule.rule} ${how}`.trim();
  }
  return `No platform rule; the composer's switch adds it when you turn it on. ${how}`;
};

export const describeScheduling = (capabilities) => {
  if (capabilities?.schedule_refusal) return capabilities.schedule_refusal;
  const native = capabilities?.native_scheduling;
  if (!native) {
    return "Guaardvark publishes a scheduled post at its time, so Guaardvark must be running then.";
  }
  const only = native.visibilities?.length ? ` ${native.visibilities.join(" or ")}` : "";
  const lead = native.min_lead_seconds
    ? ` when the time is more than ${describeWindow(native.min_lead_seconds)} away`
    : "";
  return (
    `The platform holds a scheduled${only} post and publishes it itself (${native.field})${lead}. ` +
    "Otherwise Guaardvark publishes it at its time, so Guaardvark must be running then."
  );
};

export const describeDelete = (capabilities) => {
  if (!capabilities?.deletable) return "Posts cannot be deleted from Guaardvark.";
  const windowSeconds = capabilities.delete_window_seconds;
  return windowSeconds
    ? `Delete on platform works for ${describeWindow(windowSeconds)} after posting.`
    : "Delete on platform is available for every post.";
};

const STATUS_LABELS = {
  awaiting_approval: "Waiting for approval",
  scheduled: "Scheduled",
  queued: "Queued",
  processing: "Sending",
  posted: "Posted",
  failed: "Failed",
  rejected: "Rejected",
  cancelled: "Cancelled",
  deleted: "Deleted on platform",
};

export const STATUS_TONE = {
  awaiting_approval: "warn",
  scheduled: "info",
  queued: "info",
  processing: "info",
  posted: "ok",
  failed: "error",
  rejected: "neutral",
  cancelled: "neutral",
  deleted: "neutral",
};

export const statusLabel = (record, platformLabel) => {
  if (record.status === "scheduled" && record.scheduled_natively) {
    return `Held by ${platformLabel || record.platform}`;
  }
  return STATUS_LABELS[record.status] || record.status;
};

/**
 * The strictest (smallest positive) value of a numeric capability across the
 * targets, or null when none declares one.
 */
export const strictestLimit = (specs, key) => {
  const limits = (specs || [])
    .map((s) => s?.capabilities?.[key])
    .filter((n) => typeof n === "number" && n > 0);
  return limits.length ? Math.min(...limits) : null;
};

/** The visibilities every target that declares any accepts. */
export const sharedVisibilities = (specs) => {
  const sets = (specs || [])
    .map((s) => s?.capabilities?.visibilities || [])
    .filter((v) => v.length);
  if (!sets.length) return [];
  return sets.reduce((acc, cur) => acc.filter((v) => cur.includes(v)));
};

/**
 * The visibility a post to this provider is sent with when the person picks
 * none: its declared default. The composer shows it selected and sends it.
 */
export const defaultVisibility = (spec) => spec?.capabilities?.default_visibility || "";

/**
 * The visibilities a post to this connection can take.
 * @returns {Array<{ value: string, disabled: boolean, reason: string }>}
 */
export const visibilityChoices = (spec) =>
  (spec?.capabilities?.visibilities || []).map((value) => ({ value, disabled: false, reason: "" }));

const joinWords = (words) =>
  words.length < 2 ? words.join("") : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;

/**
 * One line for the targets that offer a single visibility, which get no
 * select, e.g. "Public on Bluesky and Discord: the only visibility they offer."
 * @param {Array<{ label: string, value: string }>} targets
 */
export const fixedVisibilityNote = (targets) => {
  const byValue = new Map();
  for (const target of targets || []) {
    byValue.set(target.value, [...(byValue.get(target.value) || []), target.label]);
  }
  return [...byValue.entries()]
    .map(([value, labels]) => {
      const named = value ? `${value[0].toUpperCase()}${value.slice(1)}` : value;
      const offer = labels.length === 1 ? "it offers" : "they offer";
      return `${named} on ${joinWords(labels)}: the only visibility ${offer}.`;
    })
    .join(" ");
};

/**
 * The per-post fields the selected targets declare, merged by name: one input
 * serves every target that asks for it, required when any of them requires it.
 * @returns {Array<object & { for: string[] }>}
 */
export const mergePostFields = (specs) => {
  const byName = new Map();
  for (const spec of specs || []) {
    for (const field of spec?.post_fields || []) {
      const entry = byName.get(field.name) || { ...field, required: false, for: [] };
      entry.required = entry.required || Boolean(field.required);
      entry.for.push(spec.label || spec.provider);
      byName.set(field.name, entry);
    }
  }
  return [...byName.values()];
};

/**
 * Whether Delete on platform applies to this record now.
 * @returns {{ show: boolean, ok: boolean, reason: string }}
 */
export const deleteOnPlatform = (record, capabilities, now = Date.now()) => {
  const heldByPlatform = record.status === "scheduled" && record.scheduled_natively;
  if (!capabilities?.deletable || !record.remote_id || !(record.status === "posted" || heldByPlatform)) {
    return { show: false, ok: false, reason: "" };
  }
  const windowSeconds = capabilities.delete_window_seconds;
  if (windowSeconds && record.posted_at && !heldByPlatform) {
    const age = (now - new Date(record.posted_at).getTime()) / 1000;
    if (age > windowSeconds) {
      return {
        show: true,
        ok: false,
        reason: `The platform allows deleting only within ${describeWindow(windowSeconds)} of posting.`,
      };
    }
  }
  return { show: true, ok: true, reason: "" };
};
