import { useMemo } from "react";
import { useLocation, useParams } from "react-router-dom";
import { extensionNavGroups, extensionPageContext } from "../extensions";
import { buildNavCatalog, matchCatalogItem } from "../config/navCatalog";
import brand from "../config/brand";

// Entity types for routes that open a kind of record; the page label itself
// comes from the navigation catalog so the chip says what the sidebar says.
const CORE_ROUTE_MAP = {
  "/":                    { page: "Dashboard", entityType: null },
  "/dashboard":           { page: "Dashboard", entityType: null },
  "/chat":                { page: "Chat", entityType: null },
  "/documents":           { page: "Files", entityType: "document" },
  "/documents/bulk-import": { page: "Bulk Import", entityType: "document" },
  "/tasks":               { page: "Tasks", entityType: "task" },
  "/projects":            { page: "Projects", entityType: "project" },
  "/clients":             { page: "Clients", entityType: "client" },
  "/websites":            { page: "Websites", entityType: "website" },
  "/images":              { page: "Media", entityType: "image" },
  "/video":               { page: "Video Gen", entityType: "video" },
  "/batch-images":        { page: "Batch Images", entityType: "image" },
  "/rules":               { page: "Rules", entityType: "rule" },
  "/tools":               { page: "Tools", entityType: "tool" },
  "/agents":              { page: "Agents", entityType: "agent" },
  "/training":            { page: "Training", entityType: "training" },
  "/file-generation":     { page: "File Generation", entityType: null },
  "/settings":            { page: "Settings", entityType: null },
  "/progress-test":       { page: "Progress Test", entityType: null },
  "/dev-tools":           { page: "System Dashboard", entityType: null },
  "/plugins":             { page: "Plugins", entityType: "plugin" },
  "/code-editor":         { page: "Code Editor", entityType: "code" },
  "/upload":              { page: "Upload", entityType: null },
  "/content-library":     { page: "Content Library", entityType: "content" },
  "/wordpress/sites":     { page: "WordPress Sites", entityType: "wordpress" },
  "/wordpress/pages":     { page: "WordPress Pages", entityType: "wordpress" },
};

// Parameterized routes that need regex matching
const CORE_PARAM_ROUTES = [
  { pattern: /^\/projects\/([^/]+)$/, page: "Project Detail", entityType: "project", paramName: "projectId" },
  { pattern: /^\/code-editor\/([^/]+)$/, page: "Code Editor", entityType: "code", paramName: "projectId" },
];

// Extensions add their own pages (extensions/<id>/frontend/index.jsx pageContext).
const ROUTE_MAP = { ...CORE_ROUTE_MAP, ...extensionPageContext().routeMap };
const PARAM_ROUTES = [...CORE_PARAM_ROUTES, ...extensionPageContext().paramRoutes];
const NAV_CATALOG = buildNavCatalog(extensionNavGroups(), brand.navCatalog, brand.workspaces);

/**
 * The page context for a path: {page, entityType, entityId, pathname}.
 *
 * `page` is the navigation catalog's label for the page the path is under
 * (exactly, else the longest enclosing catalog page), so every catalog route
 * has a chip and a renamed page renames its chip. The route maps add the
 * entity type and the record id; "Unknown" means no page claims the path and
 * the chip stays hidden.
 */
export function pageContextFor(pathname, { projectId = null, catalog = NAV_CATALOG, routeMap = ROUTE_MAP, paramRoutes = PARAM_ROUTES } = {}) {
  const exact = routeMap[pathname] || null;
  let param = null;
  let paramMatch = null;
  if (!exact) {
    for (const route of paramRoutes) {
      const match = pathname.match(route.pattern);
      if (match) {
        param = route;
        paramMatch = match;
        break;
      }
    }
  }
  const item = matchCatalogItem(catalog, pathname);
  const catalogIsExact = Boolean(item && (item.path === pathname || (item.path === "/" && pathname === "/dashboard")));

  let page = "Unknown";
  if (catalogIsExact) page = item.label;
  else if (exact) page = exact.page;
  else if (item) page = item.label;
  else if (param) page = param.page;

  return {
    page,
    entityType: exact?.entityType ?? param?.entityType ?? null,
    entityId: param ? (projectId || paramMatch[1] || null) : null,
    pathname,
  };
}

export const usePageContext = () => {
  const location = useLocation();
  const { projectId } = useParams();

  return useMemo(
    () => pageContextFor(location.pathname, { projectId: projectId || null }),
    [location.pathname, projectId],
  );
};
