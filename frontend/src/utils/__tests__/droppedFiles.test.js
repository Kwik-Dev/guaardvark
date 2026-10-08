import { describe, it, expect } from "vitest";
import { snapshotDrop, collectDroppedFiles, readEntryFiles, dragCarriesFiles } from "../droppedFiles";

const fileEntry = (name) => {
  const file = new File([name], name);
  return { isFile: true, isDirectory: false, name, file: (ok) => setTimeout(() => ok(file), 0) };
};

const dirEntry = (name, children, batchSize = 2) => ({
  isFile: false,
  isDirectory: true,
  name,
  createReader: () => {
    let offset = 0;
    return {
      readEntries: (ok) =>
        setTimeout(() => {
          const batch = children.slice(offset, offset + batchSize);
          offset += batch.length;
          ok(batch);
        }, 0),
    };
  },
});

// A DataTransfer the way the browser hands it to a drop handler: one item list
// object, readable during the event and empty once the handler awaits.
const dropTransfer = (entries) => {
  let live = true;
  const items = {
    get length() {
      return live ? entries.length : 0;
    },
  };
  entries.forEach((entry, index) => {
    Object.defineProperty(items, index, {
      get: () => (live
        ? { kind: "file", webkitGetAsEntry: () => (live ? entry : null), getAsFile: () => null }
        : undefined),
    });
  });
  const transfer = {
    types: ["Files"],
    items,
    get files() {
      return live ? entries.filter((e) => e.isFile).map((e) => new File([e.name], e.name)) : [];
    },
  };
  return { transfer, endEvent: () => { live = false; } };
};

describe("snapshotDrop", () => {
  it("keeps every dropped item after the event's item list is emptied", async () => {
    const { transfer, endEvent } = dropTransfer([fileEntry("a.txt"), fileEntry("b.txt"), fileEntry("c.txt")]);

    const snapshot = snapshotDrop(transfer);
    await Promise.resolve();
    endEvent();
    const files = await collectDroppedFiles(snapshot);

    expect(files.map((f) => f.relativePath)).toEqual(["a.txt", "b.txt", "c.txt"]);
  });

  it("falls back to dataTransfer.files when the items expose nothing", async () => {
    const a = new File(["x"], "a.txt");
    const b = new File(["y"], "b.txt");
    const snapshot = snapshotDrop({ types: ["Files"], items: [], files: [a, b] });

    const files = await collectDroppedFiles(snapshot);

    expect(files.map((f) => f.file)).toEqual([a, b]);
  });

  it("takes file items without an entry as plain files", async () => {
    const loose = new File(["z"], "loose.txt");
    const snapshot = snapshotDrop({
      types: ["Files"],
      items: [{ kind: "file", getAsFile: () => loose }, { kind: "string" }],
      files: [loose],
    });

    expect(await collectDroppedFiles(snapshot)).toEqual([{ file: loose, relativePath: "loose.txt" }]);
  });
});

describe("readEntryFiles", () => {
  it("walks folders in every batch, each file once, with paths under the dropped folder", async () => {
    const tree = dirEntry("Photos", [
      fileEntry("1.jpg"),
      fileEntry("2.jpg"),
      dirEntry("Trip", [fileEntry("3.jpg")]),
      fileEntry("4.jpg"),
    ]);

    const files = await readEntryFiles(tree);

    expect(files.map((f) => f.relativePath)).toEqual([
      "Photos/1.jpg",
      "Photos/2.jpg",
      "Photos/Trip/3.jpg",
      "Photos/4.jpg",
    ]);
  });
});

describe("dragCarriesFiles", () => {
  it("is true only for drags from the operating system", () => {
    expect(dragCarriesFiles({ dataTransfer: { types: ["Files"] } })).toBe(true);
    expect(dragCarriesFiles({ dataTransfer: { types: ["text/plain"] } })).toBe(false);
    expect(dragCarriesFiles({})).toBe(false);
  });
});
