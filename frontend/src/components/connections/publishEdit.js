// frontend/src/components/connections/publishEdit.js
// Reading and editing a publish that waits for approval: who drafted it, what
// a person changed since, and the edit request built from the dialog's fields.
// The backend (PATCH /api/connections/publishes/<id>) validates and stores it.
import { scheduledIsoOf, toLocalInput } from "../social/PostFields";

// requested_by values whose text a person did not write in the composer.
const DRAFTED_BY = {
  chat: "Chat drafted this post.",
  mcp: "An AI tool connected over MCP drafted this post.",
  schedule: "A schedule queued this post.",
  unknown: "This post came from a caller that did not say what it is.",
};

/** One sentence on who pre-filled this post, or null when a person composed it. */
export const draftedBy = (record) => DRAFTED_BY[record?.requested_by] || null;

const FIELD_WORDS = {
  body: "text",
  title: "title",
  link_url: "link",
  tags: "tags",
  visibility: "visibility",
  post_options: "post fields",
  ai_disclosure: "AI label",
  scheduled_at: "time",
};

/** The edited fields of a record in plain words, e.g. "text, time". */
export const editedFieldWords = (record) =>
  (record?.edited_fields || []).map((name) => FIELD_WORDS[name] || name).join(", ");

/** The dialog's starting values for a record. */
export const editableValues = (record) => {
  const scheduled = record?.scheduled_at ? new Date(record.scheduled_at) : null;
  return {
    title: record?.title || "",
    body: record?.body || "",
    linkUrl: record?.link_url || "",
    tags: (record?.tags || []).join(", "),
    visibility: record?.visibility || "",
    aiDisclosure: Boolean(record?.ai_disclosure),
    postOptions: { ...(record?.post_options || {}) },
    scheduleOn: Boolean(scheduled && !Number.isNaN(scheduled.getTime())),
    scheduledAt: scheduled && !Number.isNaN(scheduled.getTime()) ? toLocalInput(scheduled) : "",
  };
};

const splitTags = (text) =>
  (text || "")
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);

const sameOptions = (a, b) => {
  const keys = new Set([...Object.keys(a || {}), ...Object.keys(b || {})]);
  return [...keys].every((k) => (a?.[k] ?? "") === (b?.[k] ?? ""));
};

/**
 * The fields of an edit request that differ from where the dialog started.
 * Untouched fields stay out, so a time already passed is not sent back as a
 * new one.
 */
export const editChanges = (initial, values) => {
  const changes = {};
  if (values.body !== initial.body) changes.body = values.body;
  if (values.title !== initial.title) changes.title = values.title || null;
  if (values.linkUrl !== initial.linkUrl) changes.link_url = values.linkUrl.trim() || null;
  if (splitTags(values.tags).join(",") !== splitTags(initial.tags).join(",")) {
    changes.tags = splitTags(values.tags);
  }
  if (values.visibility !== initial.visibility) changes.visibility = values.visibility || null;
  if (!sameOptions(values.postOptions, initial.postOptions)) changes.post_options = values.postOptions;
  if (values.aiDisclosure !== initial.aiDisclosure) changes.ai_disclosure = values.aiDisclosure;
  if (values.scheduleOn !== initial.scheduleOn || (values.scheduleOn && values.scheduledAt !== initial.scheduledAt)) {
    changes.scheduled_at = scheduledIsoOf(values.scheduleOn, values.scheduledAt);
  }
  return changes;
};

/**
 * The text with the AI label line added or removed, where the label is a line
 * in the post, so the text box shows what goes out.
 */
export const withDisclosureLine = (body, on, rule) => {
  if (!rule || rule.mechanism !== "text" || !rule.text) return body;
  const trimmed = (body || "").replace(/\s+$/, "");
  const has = trimmed.endsWith(rule.text);
  if (on && !has) return trimmed ? `${trimmed}\n\n${rule.text}` : rule.text;
  if (!on && has) return trimmed.slice(0, -rule.text.length).replace(/\s+$/, "");
  return body;
};
