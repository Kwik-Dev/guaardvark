import React, { Suspense } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import lazyWithReload, {
  RELOAD_WINDOW_MS,
  isStaleModuleError,
  reloadOnce,
  reloadOnceForStaleModule,
} from "./lazyWithReload";

const memoryStorage = () => {
  const data = new Map();
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
  };
};

const STALE = new TypeError(
  "Failed to fetch dynamically imported module: http://localhost:5173/src/pages/SettingsPage.jsx",
);

class Catch extends React.Component {
  state = { error: null };
  static getDerivedStateFromError(error) {
    return { error };
  }
  render() {
    return this.state.error ? <div>caught: {this.state.error.message}</div> : this.props.children;
  }
}

describe("isStaleModuleError", () => {
  it.each([
    "Failed to fetch dynamically imported module: http://x/src/a.jsx",
    "Importing a module script failed.",
    "error loading dynamically imported module: http://x/assets/a.js",
    "504 (Outdated Optimize Dep)",
  ])("matches %s", (message) => {
    expect(isStaleModuleError(new Error(message))).toBe(true);
  });

  it("ignores unrelated errors and non-errors", () => {
    expect(isStaleModuleError(new Error("Cannot read properties of undefined"))).toBe(false);
    expect(isStaleModuleError(null)).toBe(false);
    expect(isStaleModuleError({})).toBe(false);
  });
});

describe("reloadOnce", () => {
  let storage;
  let reload;
  beforeEach(() => {
    storage = memoryStorage();
    reload = vi.fn();
  });

  it("reloads once, refuses again inside the window, allows again after it", () => {
    expect(reloadOnce({ storage, reload, now: 1_000_000 })).toBe(true);
    expect(reloadOnce({ storage, reload, now: 1_000_000 + RELOAD_WINDOW_MS - 1 })).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(reloadOnce({ storage, reload, now: 1_000_000 + RELOAD_WINDOW_MS })).toBe(true);
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("does not reload when there is no storage to guard the loop", () => {
    expect(reloadOnce({ storage: null, reload })).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it("only reloads for stale-module errors", () => {
    expect(reloadOnceForStaleModule(new Error("boom"), { storage, reload })).toBe(false);
    expect(reload).not.toHaveBeenCalled();
    expect(reloadOnceForStaleModule(STALE, { storage, reload })).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });
});

describe("lazyWithReload", () => {
  let storage;
  let reload;
  beforeEach(() => {
    storage = memoryStorage();
    reload = vi.fn();
  });

  const mount = (Page) =>
    render(
      <Catch>
        <Suspense fallback={<div>loading</div>}>
          <Page />
        </Suspense>
      </Catch>,
    );

  it("reloads on a stale import and keeps the fallback up", async () => {
    const Page = lazyWithReload(() => Promise.reject(STALE), { storage, reload });
    mount(Page);
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(screen.getByText("loading")).toBeInTheDocument();
    expect(screen.queryByText(/caught/)).not.toBeInTheDocument();
  });

  it("rethrows a second stale import inside the window", async () => {
    storage.setItem("guaardvark:stale-module-reload-at", String(Date.now()));
    const Page = lazyWithReload(() => Promise.reject(STALE), { storage, reload });
    mount(Page);
    expect(await screen.findByText(/caught: Failed to fetch dynamically imported module/)).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
  });

  it("rethrows unrelated errors without reloading", async () => {
    const Page = lazyWithReload(() => Promise.reject(new Error("syntax")), { storage, reload });
    mount(Page);
    expect(await screen.findByText("caught: syntax")).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
  });

  it("renders the page when the import succeeds", async () => {
    const Page = lazyWithReload(() => Promise.resolve({ default: () => <div>page</div> }), { storage, reload });
    mount(Page);
    expect(await screen.findByText("page")).toBeInTheDocument();
  });
});
