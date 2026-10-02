// Keeps an edit form in step with a server record that keeps changing while
// the form is open (status polls, other tabs, background jobs) without ever
// overwriting what the person has typed.
//
// A synced form is { values, base, conflicts }:
//   values     what the inputs show.
//   base       the server value each field was last taken from. A field is
//              dirty while its value differs from its base.
//   conflicts  { field: serverValue } for dirty fields whose server copy has
//              since changed to something else. Neither side is picked for the
//              person; the page shows both and lets them choose.
//
// Values compare as text, with null and undefined as '', because inputs hold
// strings while the server sends numbers and nulls: a resolution typed back
// as "768" is not an edit of 768, and a cleared field is not an edit of null.
//
// Every function returns a new form (or the same object when nothing changed,
// so a React state setter can skip the render) and never mutates its input.

const asText = (value) => {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
};

export const sameFieldValue = (a, b) => asText(a) === asText(b);

export const createFormSync = (serverValues = {}) => ({
  values: { ...serverValues },
  base: { ...serverValues },
  conflicts: {},
});

export const dirtyFields = (form) =>
  Object.keys(form.values).filter((key) => !sameFieldValue(form.values[key], form.base[key]));

/** The dirty fields and their current values: what a save should send. */
export const dirtyValues = (form) =>
  Object.fromEntries(dirtyFields(form).map((key) => [key, form.values[key]]));

const shallowEqual = (a, b) => {
  const keys = Object.keys(a);
  if (keys.length !== Object.keys(b).length) return false;
  return keys.every((key) => Object.prototype.hasOwnProperty.call(b, key) && Object.is(a[key], b[key]));
};

const sameForm = (a, b) =>
  shallowEqual(a.values, b.values) && shallowEqual(a.base, b.base) && shallowEqual(a.conflicts, b.conflicts);

const orSame = (form, next) => (sameForm(form, next) ? form : next);

export const setFieldValue = (form, key, value) =>
  (Object.is(form.values[key], value) ? form : { ...form, values: { ...form.values, [key]: value } });

/**
 * Fold a fresh server copy into the form.
 *
 * - A field the person has not touched takes the server value.
 * - A dirty field keeps the person's value. If the server now holds that same
 *   value the edit is no longer pending; if the server moved to something
 *   else since the edit began, the field is listed in `conflicts`.
 * - `overwrite` names fields the person has just asked the server to rewrite
 *   (an explicit "sync from photos", say); those take the server value even
 *   when dirty. Confirm with the person before passing a dirty field here.
 *
 * Fields missing from `serverValues` are left as they are.
 */
export const mergeServerValues = (form, serverValues, { overwrite = [] } = {}) => {
  const values = { ...form.values };
  const base = { ...form.base };
  const conflicts = { ...form.conflicts };

  Object.keys(serverValues).forEach((key) => {
    const incoming = serverValues[key];
    const dirty = key in values && !sameFieldValue(values[key], base[key]);
    delete conflicts[key];
    if (!dirty || overwrite.includes(key)) {
      values[key] = incoming;
      base[key] = incoming;
    } else if (sameFieldValue(incoming, values[key])) {
      base[key] = incoming;
    } else if (!sameFieldValue(incoming, base[key])) {
      conflicts[key] = incoming;
    }
  });

  return orSame(form, { values, base, conflicts });
};

/**
 * Settle conflicts the person has answered.
 *   'server' — take the server value (the edit is dropped).
 *   'mine'   — keep the edit; the field stays dirty against the newer server
 *              value, so the next save writes the person's value over it.
 */
export const resolveConflicts = (form, choice, keys = Object.keys(form.conflicts)) => {
  if (choice !== 'server' && choice !== 'mine') {
    throw new Error(`resolveConflicts: choice must be 'server' or 'mine', got ${choice}`);
  }
  const values = { ...form.values };
  const base = { ...form.base };
  const conflicts = { ...form.conflicts };
  keys.forEach((key) => {
    if (!(key in conflicts)) return;
    const serverValue = conflicts[key];
    if (choice === 'server') values[key] = serverValue;
    base[key] = serverValue;
    delete conflicts[key];
  });
  return orSame(form, { values, base, conflicts });
};

/**
 * Record that `sentValues` reached the server. Their fields stop being dirty
 * unless the person typed more while the save was in flight; the server's own
 * copy (which may normalise what was sent) arrives with the next merge.
 */
export const markFieldsSaved = (form, sentValues) => {
  const base = { ...form.base };
  const conflicts = { ...form.conflicts };
  Object.keys(sentValues).forEach((key) => {
    base[key] = sentValues[key];
    delete conflicts[key];
  });
  return orSame(form, { values: form.values, base, conflicts });
};
