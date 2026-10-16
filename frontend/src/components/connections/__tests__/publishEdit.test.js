import { describe, expect, it } from "vitest";

import {
  draftedBy,
  editChanges,
  editableValues,
  editedFieldWords,
  withDisclosureLine,
} from "../publishEdit";

const RECORD = {
  id: 7,
  requested_by: "chat",
  title: null,
  body: "Draft from chat",
  link_url: "https://example.invalid/a",
  tags: ["one", "two"],
  visibility: "public",
  ai_disclosure: false,
  post_options: { topic: "news" },
  scheduled_at: "2026-10-23T10:00:00-04:00",
  revision: 0,
};

describe("publishEdit", () => {
  it("says who pre-filled a post, and nothing for one a person composed", () => {
    expect(draftedBy({ requested_by: "chat" })).toMatch(/^Chat drafted/);
    expect(draftedBy({ requested_by: "mcp" })).toMatch(/MCP/);
    expect(draftedBy({ requested_by: "schedule" })).toMatch(/schedule/);
    expect(draftedBy({ requested_by: "unknown" })).toMatch(/did not say/);
    expect(draftedBy({ requested_by: "ui" })).toBeNull();
  });

  it("names edited fields in plain words", () => {
    expect(editedFieldWords({ edited_fields: ["body", "scheduled_at", "ai_disclosure"] })).toBe(
      "text, time, AI label",
    );
    expect(editedFieldWords({})).toBe("");
  });

  it("sends only what changed, so an untouched time is not sent back", () => {
    const initial = editableValues(RECORD);
    expect(editChanges(initial, initial)).toEqual({});
    expect(editChanges(initial, { ...initial, tags: "one,  two" })).toEqual({});

    const changed = editChanges(initial, {
      ...initial,
      body: "A person's words",
      linkUrl: "  ",
      postOptions: { topic: "update" },
    });
    expect(changed).toEqual({
      body: "A person's words",
      link_url: null,
      post_options: { topic: "update" },
    });
  });

  it("clears the time when the schedule is switched off", () => {
    const initial = editableValues(RECORD);
    expect(initial.scheduleOn).toBe(true);
    expect(editChanges(initial, { ...initial, scheduleOn: false })).toEqual({ scheduled_at: null });
  });

  it("adds and removes the AI line where the label is a line in the post", () => {
    const rule = { mechanism: "text", text: "Made with AI." };
    expect(withDisclosureLine("Hello", true, rule)).toBe("Hello\n\nMade with AI.");
    expect(withDisclosureLine("Hello\n\nMade with AI.\n", false, rule)).toBe("Hello");
    expect(withDisclosureLine("Hello\n\nMade with AI.", true, rule)).toBe("Hello\n\nMade with AI.");
    // A platform flag leaves the text alone.
    expect(withDisclosureLine("Hello", true, { mechanism: "platform_flag", flag: "x" })).toBe("Hello");
  });
});
