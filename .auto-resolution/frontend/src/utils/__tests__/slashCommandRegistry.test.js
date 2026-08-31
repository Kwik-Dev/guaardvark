import { describe, it, expect } from "vitest";
import { getBuiltInCommands } from "../slashCommandRegistry";

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
});
