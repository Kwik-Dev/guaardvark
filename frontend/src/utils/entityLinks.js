// frontend/src/utils/entityLinks.js
// Links from a client, project, website or task to the page that shows it.
// TaskPage and ProjectDetailPage read these same parameters; change both sides together.

/** Query parameters the Tasks page reads. */
export const TASK_PARAMS = {
  taskId: "taskId",
  project: "project_id",
  client: "client_id",
  website: "website_id",
  // Present (=1) when the link asks for a new task for that entity.
  create: "new",
};

/** The ProjectDetailPage tab names, in tab order. */
export const PROJECT_TABS = ["tasks", "documents", "websites", "rules"];

/**
 * Where an entity's "Files" item leads, or null when no page lists that
 * entity's documents (the caller then leaves the item out).
 * @param {"project"|"client"|"website"} kind
 * @param {number|string} id
 */
export function entityFilesPath(kind, id) {
  if (kind === "project") return `/projects/${id}?tab=documents`;
  return null;
}

/**
 * The Tasks page with that entity's tasks shown and a new task started for it.
 * @param {"project"|"client"|"website"} kind
 * @param {number|string} id
 */
export function scheduleTaskPath(kind, id) {
  return `/tasks?${TASK_PARAMS[kind]}=${encodeURIComponent(id)}&${TASK_PARAMS.create}=1`;
}

/** The Tasks page with one task open. */
export function taskPath(id) {
  return `/tasks?${TASK_PARAMS.taskId}=${encodeURIComponent(id)}`;
}

/**
 * The "Files" and "Schedule Task" menu items for an entity, for EntityContextMenu.
 * @param {"project"|"client"|"website"} kind
 * @param {number|string} id
 * @param {function(string): void} navigate
 */
export function entityLinkActions(kind, id, navigate) {
  const files = entityFilesPath(kind, id);
  return [
    files && { label: "Files", onClick: () => navigate(files), dividerBefore: true },
    {
      label: "Schedule Task",
      onClick: () => navigate(scheduleTaskPath(kind, id)),
      dividerBefore: !files,
    },
  ].filter(Boolean);
}
