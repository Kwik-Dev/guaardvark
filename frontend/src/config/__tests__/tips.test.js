import { describe, it, expect } from "vitest";
import { TIPS } from "../tips";
import { CORE_NAV_CATALOG } from "../navCatalog";
import { TIP_ACTIONS } from "../../components/common/DidYouKnowTip";

const knownPaths = new Set([
  "/dashboard",
  ...CORE_NAV_CATALOG.filter((item) => item.path).map((item) => item.path),
]);

describe("TIPS", () => {
  it("has between ten and fifteen tips", () => {
    expect(TIPS.length).toBeGreaterThanOrEqual(10);
    expect(TIPS.length).toBeLessThanOrEqual(15);
  });

  it("gives every tip a unique id and short text", () => {
    const ids = TIPS.map((tip) => tip.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const tip of TIPS) {
      expect(tip.id).toMatch(/^[a-z0-9-]+$/);
      expect(tip.text.trim().length).toBeGreaterThan(20);
      expect(tip.text.length).toBeLessThanOrEqual(180);
    }
  });

  it("points Show me at real pages and known actions", () => {
    for (const tip of TIPS) {
      if (tip.route) expect(knownPaths).toContain(tip.route.split("#")[0]);
      if (tip.action) expect(Object.keys(TIP_ACTIONS)).toContain(tip.action);
      expect(Boolean(tip.route && tip.action)).toBe(false);
    }
  });
});
