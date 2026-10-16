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
