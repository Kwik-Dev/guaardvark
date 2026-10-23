// Replace-all over the editor's open tabs, used by the Search card.

const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** A tab the editor lets the user change (not a live repository view). */
export const isEditableTab = (tab) =>
  Boolean(tab) && typeof tab.content === "string" && !tab.readOnly && tab.source !== "live_repo";

/**
 * Replace every case-insensitive occurrence of `term` with `replacement` in
 * the editable tabs. Returns the new tab list (untouched tabs keep their
 * identity) and the counts, so the caller can report what happened.
 */
export function replaceInTabs(tabs, term, replacement) {
  if (!Array.isArray(tabs) || !term) return { tabs: tabs || [], replaced: 0, files: 0 };
  const pattern = new RegExp(escapeRegExp(term), "gi");
  let replaced = 0;
  let files = 0;
  const next = tabs.map((tab) => {
    if (!isEditableTab(tab)) return tab;
    const hits = tab.content.match(pattern);
    if (!hits) return tab;
    replaced += hits.length;
    files += 1;
    return { ...tab, content: tab.content.replace(pattern, () => replacement), isModified: true };
  });
  return { tabs: next, replaced, files };
}
