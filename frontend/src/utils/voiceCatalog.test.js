import { describe, it, expect } from "vitest";
import { catalogGroups, indexVoices, voiceStatus } from "./voiceCatalog";

const answer = {
  kokoro: {
    default: "af_heart",
    groups: [
      { label: "American Female", voices: [
        { id: "af_heart", label: "Heart (default)", installed: true },
        { id: "af_bella", label: "Bella", installed: false },
      ] },
      { label: "British Male", voices: [{ id: "bm_george", label: "George" }] },
    ],
  },
};

describe("voiceCatalog", () => {
  const index = indexVoices(catalogGroups(answer));

  it("reads the groups of a /voices answer", () => {
    expect(catalogGroups(answer)).toHaveLength(2);
    expect(catalogGroups({ kokoro: { groups: [] } })).toBeNull();
    expect(catalogGroups(null)).toBeNull();
    expect(index.get("af_bella")).toMatchObject({ label: "Bella", group: "American Female" });
  });

  it("treats nothing set and the literal default as the automatic voice", () => {
    expect(voiceStatus("", index).state).toBe("default");
    expect(voiceStatus(null, index).state).toBe("default");
    expect(voiceStatus("default", index).state).toBe("default");
  });

  it("says whether a catalog voice is installed", () => {
    expect(voiceStatus("af_heart", index).state).toBe("installed");
    expect(voiceStatus("af_bella", index).state).toBe("not_installed");
    // An older plugin sends no flag; nothing is claimed missing.
    expect(voiceStatus("bm_george", index).state).toBe("installed");
  });

  it("marks an id the catalog does not list as invalid, exactly as renders do", () => {
    expect(voiceStatus("af_bellla", index)).toEqual({ state: "invalid", id: "af_bellla" });
    expect(voiceStatus("AF_HEART", index).state).toBe("invalid");
    expect(voiceStatus(" af_heart ", index).state).toBe("installed");
  });

  it("claims nothing when the catalog could not be loaded", () => {
    expect(voiceStatus("af_bellla", null)).toEqual({ state: "unknown", id: "af_bellla" });
  });
});
