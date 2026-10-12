import { describe, it, expect } from "vitest";
import { pageContextFor } from "../usePageContext";
import { CORE_NAV_CATALOG } from "../../config/navCatalog";

const catalog = [
  { id: "dashboard", kind: "page", path: "/", label: "Dashboard", workspace: "home" },
  { id: "files", kind: "page", path: "/documents", label: "Files", workspace: "library" },
  { id: "agents", kind: "page", path: "/agents", label: "Agents", workspace: "agents" },
  { id: "pin", kind: "page", path: "/notifications", label: "Pin" },
  { id: "stop", kind: "action", label: "Stop" },
];
const routeMap = {
  "/documents": { page: "Documents", entityType: "document" },
  "/documents/bulk-import": { page: "Bulk Import", entityType: "document" },
  "/settings": { page: "Settings", entityType: null },
};
const paramRoutes = [
  { pattern: /^\/projects\/([^/]+)$/, page: "Project Detail", entityType: "project" },
];
const ctx = (pathname, projectId = null) => pageContextFor(pathname, { projectId, catalog, routeMap, paramRoutes });

describe("pageContextFor", () => {
  it("names the page the way the navigation catalog does, keeping the route's entity type", () => {
    expect(ctx("/documents")).toEqual({ page: "Files", entityType: "document", entityId: null, pathname: "/documents" });
    expect(ctx("/")).toMatchObject({ page: "Dashboard", entityType: null });
    expect(ctx("/dashboard")).toMatchObject({ page: "Dashboard" });
  });

  it("names the code pages from the app's own catalog and route map", () => {
    expect(pageContextFor("/swarm")).toMatchObject({ page: "Swarm", entityType: null });
    expect(pageContextFor("/system-map")).toMatchObject({ page: "System Map", entityType: null });
    expect(pageContextFor("/self-improvement")).toMatchObject({ page: "Self-improvement", entityType: null });
  });

  it("covers a catalog route the route map never listed", () => {
    expect(ctx("/agents")).toMatchObject({ page: "Agents", entityType: null, entityId: null });
    expect(ctx("/agents/memory")).toMatchObject({ page: "Agents", entityId: null });
  });

  it("prefers a more specific route-map page over the enclosing catalog page", () => {
    expect(ctx("/documents/bulk-import")).toMatchObject({ page: "Bulk Import", entityType: "document" });
    expect(ctx("/settings")).toMatchObject({ page: "Settings" });
  });

  it("reads the record id from a parameterized route", () => {
    expect(ctx("/projects/7")).toEqual({ page: "Project Detail", entityType: "project", entityId: "7", pathname: "/projects/7" });
    expect(ctx("/projects/7", "9")).toMatchObject({ entityId: "9" });
  });

  it("is Unknown, so the chip hides, where no page claims the path", () => {
    expect(ctx("/nowhere")).toMatchObject({ page: "Unknown", entityType: null, entityId: null });
    expect(ctx("/notifications")).toMatchObject({ page: "Unknown" });
  });

  it("gives every core catalog page its own label", () => {
    for (const item of CORE_NAV_CATALOG) {
      if (item.kind !== "page" || !item.path || !item.workspace) continue;
      expect(pageContextFor(item.path, { catalog: CORE_NAV_CATALOG, routeMap: {}, paramRoutes: [] }).page).toBe(item.label);
    }
    expect(pageContextFor("/documents", { catalog: CORE_NAV_CATALOG, routeMap: {}, paramRoutes: [] }).page).toBe("Files");
  });
});
