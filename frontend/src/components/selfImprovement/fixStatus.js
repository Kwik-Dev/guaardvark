// A pending fix waits for a person while it is proposed or triaged (to review)
// or approved (to apply): the groups FixesModal acts on.
export const WAITING_FIX_STATUSES = new Set(["proposed", "triaged", "approved"]);

export const waitingFixCount = (fixes) =>
  Array.isArray(fixes) ? fixes.filter((fix) => WAITING_FIX_STATUSES.has(fix.status)).length : 0;
