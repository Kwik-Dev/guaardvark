import React, { useState } from "react";
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import useUnsavedChangesGuard, { leavesPath, targetPathname } from "./useUnsavedChangesGuard";

describe("leavesPath", () => {
  it("is true for another path", () => {
    expect(leavesPath({ pathname: "/cast", search: "", hash: "" }, "/cast/7")).toBe(true);
    expect(leavesPath("/audio?models=kokoro", "/cast/7")).toBe(true);
  });

  it("is false for a search or hash change on the same path", () => {
    expect(leavesPath({ pathname: "/cast/7", search: "?tab=2", hash: "" }, "/cast/7")).toBe(false);
    expect(leavesPath("/cast/7#samples", "/cast/7")).toBe(false);
  });

  it("ignores a trailing slash", () => {
    expect(leavesPath("/cast/7/", "/cast/7")).toBe(false);
  });

  it("is false when the target has no pathname", () => {
    expect(targetPathname({ search: "?x=1" })).toBe(null);
    expect(leavesPath({ search: "?x=1" }, "/cast/7")).toBe(false);
  });
});

let navigate;
let setDirty;

const Editor = () => {
  const [dirty, setDirtyState] = useState(false);
  setDirty = setDirtyState;
  navigate = useNavigate();
  useUnsavedChangesGuard(dirty);
  return <p>editor</p>;
};

const Other = () => {
  navigate = useNavigate();
  return <p>other</p>;
};

const Where = () => {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
};

const renderAt = (path) =>
  render(
    <MemoryRouter initialEntries={["/start", path]} initialIndex={1}>
      <Where />
      <Routes>
        <Route path="/cast/:id" element={<Editor />} />
        <Route path="*" element={<Other />} />
      </Routes>
    </MemoryRouter>,
  );

describe("useUnsavedChangesGuard", () => {
  let confirmSpy;

  beforeEach(() => {
    confirmSpy = vi.spyOn(window, "confirm");
  });

  afterEach(() => {
    confirmSpy.mockRestore();
  });

  it("navigates without asking while nothing is unsaved", () => {
    renderAt("/cast/7");
    act(() => navigate("/cast"));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId("where").textContent).toBe("/cast");
  });

  it("stays when the person cancels leaving with unsaved edits", () => {
    confirmSpy.mockReturnValue(false);
    renderAt("/cast/7");
    act(() => setDirty(true));
    act(() => navigate("/cast"));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("where").textContent).toBe("/cast/7");
    expect(screen.getByText("editor")).toBeTruthy();
  });

  it("leaves when the person confirms", () => {
    confirmSpy.mockReturnValue(true);
    renderAt("/cast/7");
    act(() => setDirty(true));
    act(() => navigate("/audio"));
    expect(screen.getByTestId("where").textContent).toBe("/audio");
    expect(screen.getByText("other")).toBeTruthy();
  });

  it("asks before going back in history", () => {
    confirmSpy.mockReturnValue(false);
    renderAt("/cast/7");
    act(() => setDirty(true));
    act(() => navigate(-1));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("where").textContent).toBe("/cast/7");
  });

  it("does not ask about a search-param change on the same page", () => {
    renderAt("/cast/7");
    act(() => setDirty(true));
    act(() => navigate("/cast/7?tab=2", { replace: true }));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId("where").textContent).toBe("/cast/7?tab=2");
  });

  it("blocks closing the tab only while edits are unsaved", () => {
    renderAt("/cast/7");
    const unload = () => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };
    expect(unload()).toBe(false);
    act(() => setDirty(true));
    expect(unload()).toBe(true);
    act(() => setDirty(false));
    expect(unload()).toBe(false);
  });

  it("keeps asking while any of several guards holds, released in either order", () => {
    let setA;
    let setB;
    const Guard = ({ when }) => {
      useUnsavedChangesGuard(when);
      return null;
    };
    const Rows = () => {
      const [a, setAState] = useState(true);
      const [b, setBState] = useState(true);
      setA = setAState;
      setB = setBState;
      navigate = useNavigate();
      return (
        <>
          <Guard when={a} />
          <Guard when={b} />
        </>
      );
    };
    confirmSpy.mockReturnValue(false);
    render(
      <MemoryRouter initialEntries={["/rows"]}>
        <Where />
        <Routes>
          <Route path="/rows" element={<Rows />} />
          <Route path="*" element={<Other />} />
        </Routes>
      </MemoryRouter>,
    );
    act(() => setA(false));
    act(() => navigate("/away"));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    act(() => setA(true));
    act(() => setB(false));
    act(() => navigate("/away"));
    expect(confirmSpy).toHaveBeenCalledTimes(2);
    act(() => setA(false));
    act(() => navigate("/away"));
    expect(confirmSpy).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("where").textContent).toBe("/away");
  });

  it("stops asking once the page is gone", () => {
    confirmSpy.mockReturnValue(true);
    renderAt("/cast/7");
    act(() => setDirty(true));
    act(() => navigate("/audio"));
    confirmSpy.mockClear();
    act(() => navigate("/cast"));
    expect(confirmSpy).not.toHaveBeenCalled();
  });
});
