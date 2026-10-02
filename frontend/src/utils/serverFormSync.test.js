import { describe, it, expect } from "vitest";

import {
  createFormSync,
  dirtyFields,
  dirtyValues,
  markFieldsSaved,
  mergeServerValues,
  resolveConflicts,
  sameFieldValue,
  setFieldValue,
} from "./serverFormSync";

const server = { name: "Ada", description: "", voice_id: "af_bella", bible: "tall" };

describe("sameFieldValue", () => {
  it("treats null, undefined and empty text alike", () => {
    expect(sameFieldValue(null, "")).toBe(true);
    expect(sameFieldValue(undefined, "")).toBe(true);
    expect(sameFieldValue(null, undefined)).toBe(true);
  });

  it("compares numbers with the text an input holds", () => {
    expect(sameFieldValue(768, "768")).toBe(true);
    expect(sameFieldValue(768, "769")).toBe(false);
  });
});

describe("dirty tracking", () => {
  it("starts clean", () => {
    expect(dirtyFields(createFormSync(server))).toEqual([]);
  });

  it("reports only the fields the person changed", () => {
    const form = setFieldValue(createFormSync(server), "name", "Ada L.");
    expect(dirtyFields(form)).toEqual(["name"]);
    expect(dirtyValues(form)).toEqual({ name: "Ada L." });
  });

  it("is clean again when an edit is typed back to the server value", () => {
    let form = setFieldValue(createFormSync(server), "name", "Ada L.");
    form = setFieldValue(form, "name", "Ada");
    expect(dirtyFields(form)).toEqual([]);
  });

  it("returns the same form when a field is set to its current value", () => {
    const form = createFormSync(server);
    expect(setFieldValue(form, "name", "Ada")).toBe(form);
  });
});

describe("mergeServerValues", () => {
  it("refreshes fields the person has not touched", () => {
    const form = mergeServerValues(createFormSync(server), { ...server, bible: "tall, red coat" });
    expect(form.values.bible).toBe("tall, red coat");
    expect(dirtyFields(form)).toEqual([]);
  });

  it("never overwrites an edit when the server copy is unchanged", () => {
    const edited = setFieldValue(createFormSync(server), "description", "half-typed");
    const form = mergeServerValues(edited, server);
    expect(form.values.description).toBe("half-typed");
    expect(form.conflicts).toEqual({});
  });

  it("returns the same form when the server sends what it already has", () => {
    const edited = setFieldValue(createFormSync(server), "description", "half-typed");
    expect(mergeServerValues(edited, server)).toBe(edited);
  });

  it("keeps an edit and flags a conflict when the server changed underneath it", () => {
    const edited = setFieldValue(createFormSync(server), "name", "Ada L.");
    const form = mergeServerValues(edited, { ...server, name: "Countess" });
    expect(form.values.name).toBe("Ada L.");
    expect(form.conflicts).toEqual({ name: "Countess" });
    expect(dirtyFields(form)).toEqual(["name"]);
  });

  it("refreshes untouched fields while keeping a conflicted one", () => {
    const edited = setFieldValue(createFormSync(server), "name", "Ada L.");
    const form = mergeServerValues(edited, { ...server, name: "Countess", bible: "short" });
    expect(form.values.bible).toBe("short");
    expect(form.values.name).toBe("Ada L.");
  });

  it("clears the edit when the server already holds the typed value", () => {
    const edited = setFieldValue(createFormSync(server), "name", "Ada L.");
    const form = mergeServerValues(edited, { ...server, name: "Ada L." });
    expect(dirtyFields(form)).toEqual([]);
    expect(form.conflicts).toEqual({});
  });

  it("drops a conflict once the server returns to the value the edit began from", () => {
    const edited = setFieldValue(createFormSync(server), "name", "Ada L.");
    const conflicted = mergeServerValues(edited, { ...server, name: "Countess" });
    const form = mergeServerValues(conflicted, server);
    expect(form.conflicts).toEqual({});
    expect(form.values.name).toBe("Ada L.");
  });

  it("lets an explicit server rewrite replace a dirty field", () => {
    const edited = setFieldValue(createFormSync(server), "bible", "my notes");
    const form = mergeServerValues(edited, { ...server, bible: "from photos" }, { overwrite: ["bible"] });
    expect(form.values.bible).toBe("from photos");
    expect(dirtyFields(form)).toEqual([]);
    expect(form.conflicts).toEqual({});
  });

  it("does not count a number typed back as text as an edit", () => {
    const settings = { resolution: 768, steps: null };
    let form = setFieldValue(createFormSync(settings), "resolution", "768");
    form = setFieldValue(form, "steps", "");
    expect(dirtyFields(form)).toEqual([]);
    expect(mergeServerValues(form, { resolution: 1024, steps: null }).values.resolution).toBe(1024);
  });

  it("leaves fields the server copy does not mention alone", () => {
    const edited = setFieldValue(createFormSync(server), "voice_id", "am_adam");
    const form = mergeServerValues(edited, { name: "Ada" });
    expect(form.values.voice_id).toBe("am_adam");
  });
});

describe("resolveConflicts", () => {
  const conflicted = () =>
    mergeServerValues(setFieldValue(createFormSync(server), "name", "Ada L."), { ...server, name: "Countess" });

  it("'server' takes the server value and drops the edit", () => {
    const form = resolveConflicts(conflicted(), "server");
    expect(form.values.name).toBe("Countess");
    expect(dirtyFields(form)).toEqual([]);
    expect(form.conflicts).toEqual({});
  });

  it("'mine' keeps the edit dirty against the newer server value", () => {
    const form = resolveConflicts(conflicted(), "mine");
    expect(form.values.name).toBe("Ada L.");
    expect(dirtyFields(form)).toEqual(["name"]);
    expect(form.conflicts).toEqual({});
    // The same server copy on the next poll is not a new conflict.
    expect(mergeServerValues(form, { ...server, name: "Countess" }).conflicts).toEqual({});
  });

  it("rejects an unknown choice", () => {
    expect(() => resolveConflicts(conflicted(), "both")).toThrow();
  });
});

describe("markFieldsSaved", () => {
  it("makes saved fields clean and lets the server's copy in afterwards", () => {
    const edited = setFieldValue(createFormSync(server), "name", "  Ada L. ");
    const saved = markFieldsSaved(edited, dirtyValues(edited));
    expect(dirtyFields(saved)).toEqual([]);
    // The server trims what it stores; the clean field takes that.
    const form = mergeServerValues(saved, { ...server, name: "Ada L." });
    expect(form.values.name).toBe("Ada L.");
  });

  it("keeps typing that happened while the save was in flight", () => {
    const edited = setFieldValue(createFormSync(server), "name", "Ada L.");
    const sent = dirtyValues(edited);
    const typedMore = setFieldValue(edited, "name", "Ada Lovelace");
    const saved = markFieldsSaved(typedMore, sent);
    expect(dirtyFields(saved)).toEqual(["name"]);
    const form = mergeServerValues(saved, { ...server, name: "Ada L." });
    expect(form.values.name).toBe("Ada Lovelace");
    expect(form.conflicts).toEqual({});
  });

  it("clears a conflict on a field the person saved over", () => {
    const conflicted = mergeServerValues(
      setFieldValue(createFormSync(server), "name", "Ada L."),
      { ...server, name: "Countess" },
    );
    expect(markFieldsSaved(conflicted, { name: "Ada L." }).conflicts).toEqual({});
  });
});
