import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../stores/useAppStore", () => ({
  useAppStore: { getState: () => ({}) },
}));
vi.mock("../../api/apiClient", () => ({ BASE_URL: "/api" }));
vi.mock("../../api/settingsService", () => ({
  getChatImageModel: vi.fn(async () => null),
  setChatImageModel: vi.fn(async () => {}),
}));

import { commandOutputFilename, executeBuiltinCommand } from "../slashCommandHandlers";

const RULE_COMMAND = { name: "/brief", handler: "rule", ruleId: 3, description: "brief" };

const run = async (args, fetchImpl) => {
  global.fetch = vi.fn(fetchImpl);
  const addMessage = vi.fn();
  const result = await executeBuiltinCommand("/brief", args, {
    addMessage,
    allCommands: [RULE_COMMAND],
  });
  return { result, addMessage, body: JSON.parse(global.fetch.mock.calls[0][1].body) };
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("commandOutputFilename", () => {
  const at = new Date("2026-10-10T14:03:09.123Z");

  it("takes output_file from the arguments the way /createfile does", () => {
    expect(commandOutputFilename("/brief", 'output_file="plan.md" write a plan', at)).toBe("plan.md");
    expect(commandOutputFilename("/brief", "outputfile=notes.txt go", at)).toBe("notes.txt");
  });

  it("otherwise names the file after the command and the time", () => {
    expect(commandOutputFilename("/brief", "write a plan", at)).toBe("brief_20261010T140309.txt");
  });

  it("uses .csv for a CSV command", () => {
    expect(commandOutputFilename("/createcsv", "ten rows", at)).toBe("createcsv_20261010T140309.csv");
  });
});

describe("a Rules-page command run from chat", () => {
  it("sends the filename and parameters the API requires", async () => {
    const { body } = await run("write a plan", async () => ({
      ok: true,
      status: 200,
      json: async () => ({ output_file: "brief_x.txt", file_size: 42, status: "success" }),
    }));
    expect(body.command_label).toBe("/brief");
    expect(body.output_filename).toMatch(/^brief_\d{8}T\d{6}\.txt$/);
    expect(body.generation_parameters).toEqual({
      args: "write a plan",
      USER_INPUT: "write a plan",
      filename: body.output_filename,
    });
  });

  it("reports what was written on success", async () => {
    const { addMessage, result } = await run("write a plan", async () => ({
      ok: true,
      status: 200,
      json: async () => ({ output_file: "brief_x.txt", file_size: 42, status: "success" }),
    }));
    expect(result).toEqual({ handled: true });
    const reply = addMessage.mock.calls.find(([m]) => m.role === "assistant")[0];
    expect(reply.content).toBe("/brief wrote brief_x.txt (42 bytes) to the outputs folder.");
  });

  it("reports a 400 as a failure instead of 'Command executed.'", async () => {
    const { addMessage } = await run("x", async () => ({
      ok: false,
      status: 400,
      json: async () => ({ error: "'command_label', 'output_filename' and 'generation_parameters' are required." }),
    }));
    const messages = addMessage.mock.calls.map(([m]) => m);
    expect(messages.some((m) => /Command executed/.test(m.content))).toBe(false);
    const failure = messages.find((m) => m.role === "system");
    expect(failure.content).toMatch(/^\/brief failed: 'command_label', 'output_filename'/);
  });

  it("reports an inactive or missing rule as the API describes it", async () => {
    const { addMessage } = await run("x", async () => ({
      ok: false,
      status: 404,
      json: async () => ({ error: "Active command rule '/brief' not found for model 'gemma4:12b'." }),
    }));
    const failure = addMessage.mock.calls.map(([m]) => m).find((m) => m.role === "system");
    expect(failure.content).toBe("/brief failed: Active command rule '/brief' not found for model 'gemma4:12b'.");
  });

  it("reports a network error as a failure", async () => {
    const { addMessage } = await run("x", async () => {
      throw new Error("Failed to fetch");
    });
    const failure = addMessage.mock.calls.map(([m]) => m).find((m) => m.role === "system");
    expect(failure.content).toBe("/brief failed: Failed to fetch");
  });
});
