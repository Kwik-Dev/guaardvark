import { describe, it, expect, beforeEach } from "vitest";
import { mergePersistedAppState, useAppStore } from "../useAppStore";

describe("useAppStore: what the tip card waits for", () => {
  beforeEach(() => {
    useAppStore.setState({ systemInfoLoaded: false, profileFirstRun: false, isFetchingSystemInfo: false });
    global.fetch.mockReset();
  });

  it("marks the profile as known once branding arrives, first run included", async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ data: { system_name: "Box", profile_first_run: true } }),
    });
    await useAppStore.getState().fetchSystemInfo();
    expect(useAppStore.getState().systemInfoLoaded).toBe(true);
    expect(useAppStore.getState().profileFirstRun).toBe(true);
  });

  it("marks it known when the fetch fails too, so a down backend does not hold tips forever", async () => {
    global.fetch.mockRejectedValue(new Error("offline"));
    await useAppStore.getState().fetchSystemInfo();
    expect(useAppStore.getState().systemInfoLoaded).toBe(true);
    expect(useAppStore.getState().profileFirstRun).toBe(false);
  });

  it("keeps tips on for a browser that saved state before the switch existed", () => {
    const merged = mergePersistedAppState({ themeName: "light" }, useAppStore.getState());
    expect(merged.tipsEnabled).toBe(true);
    expect(mergePersistedAppState({ tipsEnabled: false }, useAppStore.getState()).tipsEnabled).toBe(false);
  });
});
