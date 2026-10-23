import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../apiClient", () => ({ BASE_URL: "/api" }));
vi.mock("../projectService", () => ({ getProjects: vi.fn(), getProjectsForClient: vi.fn() }));
vi.mock("../clientService", () => ({ getClients: vi.fn() }));

import { updateEntityLinks } from "../utilService";

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(console, "error").mockImplementation(() => {});
});

describe("updateEntityLinks", () => {
  it("puts the linked ids to the entity-links route", async () => {
    global.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ success: true, message: "Successfully updated projects links for rule 4" }),
    }));
    const result = await updateEntityLinks("rule", 4, "project", [7, 9]);
    expect(global.fetch.mock.calls[0][0]).toBe("/api/entity-links/rule/4/links/projects");
    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({ linked_ids: [7, 9] });
    expect(result).toEqual({ success: true, message: "Successfully updated projects links for rule 4" });
  });

  it("throws the server's error on a 400 instead of returning a value", async () => {
    global.fetch = vi.fn(async () => ({
      ok: false,
      status: 400,
      statusText: "BAD REQUEST",
      json: async () => ({ error: "Link type projects not supported for rule" }),
    }));
    await expect(updateEntityLinks("rule", 4, "project", [7])).rejects.toThrow(
      "Link type projects not supported for rule",
    );
  });

  it("throws on a success:false body", async () => {
    global.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ success: false, error: "nope" }),
    }));
    await expect(updateEntityLinks("rule", 4, "project", [])).rejects.toThrow("nope");
  });

  it("throws on a network failure", async () => {
    global.fetch = vi.fn(async () => {
      throw new Error("Failed to fetch");
    });
    await expect(updateEntityLinks("rule", 4, "project", [])).rejects.toThrow("Failed to fetch");
  });
});
