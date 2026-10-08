import { describe, it, expect, afterEach } from "vitest";
import { installFileDropGuard } from "../fileDropGuard";

// jsdom has no DragEvent with a dataTransfer; a plain Event carries one here.
const dragEvent = (type, types) => {
  const event = new Event(type, { bubbles: true, cancelable: true });
  event.dataTransfer = { types, dropEffect: "copy" };
  return event;
};

let removeGuard;
afterEach(() => {
  removeGuard?.();
  removeGuard = undefined;
  document.body.innerHTML = "";
});

describe("installFileDropGuard", () => {
  it("cancels a file drop nothing handled, so the browser does not open the file", () => {
    removeGuard = installFileDropGuard(window);
    const plain = document.createElement("div");
    document.body.appendChild(plain);

    const over = dragEvent("dragover", ["Files"]);
    plain.dispatchEvent(over);
    const drop = dragEvent("drop", ["Files"]);
    plain.dispatchEvent(drop);

    expect(over.defaultPrevented).toBe(true);
    expect(over.dataTransfer.dropEffect).toBe("none");
    expect(drop.defaultPrevented).toBe(true);
  });

  it("leaves a drop zone's own handling alone", () => {
    removeGuard = installFileDropGuard(window);
    const zone = document.createElement("div");
    document.body.appendChild(zone);
    const seen = [];
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    });
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      seen.push(e.type);
    });

    const over = dragEvent("dragover", ["Files"]);
    zone.dispatchEvent(over);
    zone.dispatchEvent(dragEvent("drop", ["Files"]));

    expect(over.dataTransfer.dropEffect).toBe("copy");
    expect(seen).toEqual(["drop"]);
  });

  it("ignores drags that carry no files", () => {
    removeGuard = installFileDropGuard(window);
    const over = dragEvent("dragover", ["text/plain"]);
    document.body.dispatchEvent(over);

    expect(over.defaultPrevented).toBe(false);
    expect(over.dataTransfer.dropEffect).toBe("copy");
  });

  it("lets a native file input take the drop", () => {
    removeGuard = installFileDropGuard(window);
    const input = document.createElement("input");
    input.type = "file";
    document.body.appendChild(input);

    const drop = dragEvent("drop", ["Files"]);
    input.dispatchEvent(drop);

    expect(drop.defaultPrevented).toBe(false);
  });

  it("stops guarding once removed", () => {
    installFileDropGuard(window)();
    const drop = dragEvent("drop", ["Files"]);
    document.body.dispatchEvent(drop);

    expect(drop.defaultPrevented).toBe(false);
  });
});
