import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  directToolOptions,
  extractCommandRules,
  getAllCommands,
  getBuiltInCommands,
  resetDbCommandsCache,
} from "../slashCommandRegistry";

describe("built-in slash commands", () => {
  it("lists music-video and film-crew next to /video", () => {
    const names = getBuiltInCommands().map((c) => c.name);
    expect(names).toContain("/video");
    expect(names).toContain("/music-video");
    expect(names).toContain("/film-crew");
    expect(names).toContain("/removebg");
    expect(names).toContain("/outpaint");
    expect(names).toContain("/inpaint");
    const mv = getBuiltInCommands().find((c) => c.name === "/music-video");
    expect(mv.usage).toContain("<song-path-or-id>");
    expect(mv.description.toLowerCase()).toContain("approve");
  });

  it("describes /websearch without naming a search service it does not use", () => {
    const ws = getBuiltInCommands().find((c) => c.name === "/websearch");
    expect(ws.description.toLowerCase()).not.toContain("duckduckgo");
    expect(ws.usage).toContain("site:");
    expect(ws.usage).toContain("sitemap:");
  });
});

describe("extractCommandRules", () => {
  const rules = [{ id: 3, command_label: "/ship", description: "Ship it" }];

  it("accepts the live bare array", () => {
    expect(extractCommandRules(rules)).toEqual(rules);
  });

  it("accepts {rules} and {data: {rules}} envelopes", () => {
    expect(extractCommandRules({ rules })).toEqual(rules);
    expect(extractCommandRules({ data: { rules } })).toEqual(rules);
  });

  it("returns [] for anything else", () => {
    expect(extractCommandRules(null)).toEqual([]);
    expect(extractCommandRules({ data: {} })).toEqual([]);
    expect(extractCommandRules({ success: true })).toEqual([]);
  });
});

describe("DB command loader", () => {
  beforeEach(() => {
    resetDbCommandsCache();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    resetDbCommandsCache();
    vi.unstubAllGlobals();
  });

  it("populates custom commands from a bare array payload", async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: async () => [
        { id: 7, command_label: "/ship", description: "Ship it", name: "Ship" },
        { id: 8, name: "no label — dropped" },
      ],
    });

    const cmds = await getAllCommands();
    const custom = cmds.find((c) => c.name === "/ship");
    expect(custom).toMatchObject({
      name: "/ship",
      description: "Ship it",
      handler: "rule",
      ruleId: 7,
      category: "custom",
    });
    expect(cmds.some((c) => c.ruleId === 8)).toBe(false);
    expect(fetch).toHaveBeenCalledWith("/api/rules?type=COMMAND_RULE&is_active=true");
  });

  it("still reads the object envelopes", async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        data: { rules: [{ id: 9, command_label: "ping", description: "Ping" }] },
      }),
    });

    const cmds = await getAllCommands();
    expect(cmds.find((c) => c.ruleId === 9)).toMatchObject({
      name: "/ping",
      description: "Ping",
    });
  });
});

describe("directToolOptions", () => {
  it("carries a slash command's tool, params, command, args and image model", () => {
    expect(
      directToolOptions({
        direct_tool: "generate_image",
        direct_tool_params: { prompt: "a fox", model: "z-image-turbo" },
        slash_command: "imagine",
        slash_args: "a fox",
        image_model: "z-image-turbo",
        isVoiceMessage: false,
      })
    ).toEqual({
      direct_tool: "generate_image",
      direct_tool_params: { prompt: "a fox", model: "z-image-turbo" },
      slash_command: "imagine",
      slash_args: "a fox",
      image_model: "z-image-turbo",
    });
  });

  it("gives a command without params an empty params object", () => {
    expect(directToolOptions({ direct_tool: "read_logs", slash_command: "logs" })).toEqual({
      direct_tool: "read_logs",
      direct_tool_params: {},
      slash_command: "logs",
    });
  });

  it("adds nothing to an ordinary message", () => {
    expect(directToolOptions(undefined)).toEqual({});
    expect(directToolOptions({ documentNotice: true })).toEqual({});
  });
});
