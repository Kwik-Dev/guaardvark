import { describe, expect, it } from "vitest";

import {
  deleteOnPlatform,
  describeDisclosure,
  describeScheduling,
  describeWindow,
  statusLabel,
} from "../platformRules";

const HOUR = 3600;

describe("describeWindow", () => {
  it("names hours, days and minutes", () => {
    expect(describeWindow(HOUR)).toBe("hour");
    expect(describeWindow(48 * HOUR)).toBe("2 days");
    expect(describeWindow(1800)).toBe("30 minutes");
  });
});

describe("describeDisclosure", () => {
  it("says when a rule applies and how it is applied", () => {
    const flag = describeDisclosure({
      required_when: "generated_media",
      mechanism: "platform_flag",
      flag: "status.containsSyntheticMedia",
      rule: "Platform rule.",
    });
    expect(flag).toMatch(/^Required when the post carries media/);
    expect(flag).toContain("status.containsSyntheticMedia");
    expect(describeDisclosure({ required_when: "never", mechanism: "text", text: "Made with AI." })).toMatch(
      /^No platform rule/,
    );
  });
});

describe("describeScheduling", () => {
  it("falls back to the timer without native scheduling", () => {
    expect(describeScheduling({ native_scheduling: null })).toMatch(/Guaardvark must be running/);
    expect(
      describeScheduling({ native_scheduling: { field: "scheduled_at", min_lead_seconds: 300, visibilities: [] } }),
    ).toMatch(/publishes it itself \(scheduled_at\) when the time is more than 5 minutes away/);
  });
});

describe("statusLabel", () => {
  it("names a post the platform holds", () => {
    expect(statusLabel({ status: "scheduled", scheduled_natively: true }, "Mastodon")).toBe("Held by Mastodon");
    expect(statusLabel({ status: "scheduled", scheduled_natively: false }, "Bluesky")).toBe("Scheduled");
  });
});

describe("deleteOnPlatform", () => {
  const caps = { deletable: true, delete_window_seconds: 48 * HOUR };
  const now = new Date("2030-01-03T12:00:00Z").getTime();

  it("offers delete for a posted post inside the window", () => {
    const record = { status: "posted", remote_id: "1", posted_at: "2030-01-03T00:00:00Z" };
    expect(deleteOnPlatform(record, caps, now)).toEqual({ show: true, ok: true, reason: "" });
  });

  it("explains a closed window", () => {
    const record = { status: "posted", remote_id: "1", posted_at: "2029-12-30T00:00:00Z" };
    const result = deleteOnPlatform(record, caps, now);
    expect(result.show && !result.ok).toBe(true);
    expect(result.reason).toMatch(/2 days/);
  });

  it("hides delete where it does not apply", () => {
    expect(deleteOnPlatform({ status: "failed", remote_id: "1" }, caps, now).show).toBe(false);
    expect(deleteOnPlatform({ status: "posted", remote_id: null }, caps, now).show).toBe(false);
    expect(deleteOnPlatform({ status: "posted", remote_id: "1" }, { deletable: false }, now).show).toBe(false);
  });

  it("allows withdrawing a post the platform still holds", () => {
    const held = { status: "scheduled", scheduled_natively: true, remote_id: "s1" };
    expect(deleteOnPlatform(held, caps, now).ok).toBe(true);
  });
});
