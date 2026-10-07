import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import ErrorBoundary, { ErrorResetContext } from "./ErrorBoundary";
import { reloadOnceForStaleModule } from "../../utils/lazyWithReload";

vi.mock("../../utils/lazyWithReload", async (importOriginal) => ({
  ...(await importOriginal()),
  reloadOnceForStaleModule: vi.fn(() => false),
}));

const Boom = ({ error }) => {
  if (error) throw error;
  return <div>page ok</div>;
};

const Shell = ({ route, error }) => (
  <ErrorResetContext.Provider value={route}>
    <ErrorBoundary>
      <Boom error={error} />
    </ErrorBoundary>
  </ErrorResetContext.Provider>
);

describe("ErrorBoundary", () => {
  beforeEach(() => {
    reloadOnceForStaleModule.mockClear();
  });

  it("shows the card for a crash and clears it when the route changes", () => {
    const { rerender } = render(<Shell route="/settings" error={new Error("kaput")} />);
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try Again" })).toBeInTheDocument();

    rerender(<Shell route="/settings" error={null} />);
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();

    rerender(<Shell route="/documents" error={null} />);
    expect(screen.getByText("page ok")).toBeInTheDocument();
  });

  it("keeps a healthy page mounted across route changes", () => {
    const mounts = vi.fn();
    const Page = () => {
      React.useEffect(() => mounts(), []);
      return <div>tabbed page</div>;
    };
    const Tabs = ({ route }) => (
      <ErrorResetContext.Provider value={route}>
        <ErrorBoundary>
          <Page />
        </ErrorBoundary>
      </ErrorResetContext.Provider>
    );
    const { rerender } = render(<Tabs route="/images" />);
    rerender(<Tabs route="/video" />);
    expect(screen.getByText("tabbed page")).toBeInTheDocument();
    expect(mounts).toHaveBeenCalledTimes(1);
  });

  it("says the code was updated and offers only a reload for a stale import", () => {
    const stale = new TypeError("Failed to fetch dynamically imported module: http://x/src/pages/ToolsPage.jsx");
    render(<Shell route="/tools" error={stale} />);
    expect(screen.getByText("Guaardvark was updated")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload Page" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try Again" })).not.toBeInTheDocument();
    expect(reloadOnceForStaleModule).toHaveBeenCalledWith(stale);
  });

  it("an explicit resetKey wins over the inherited one", () => {
    const { rerender } = render(
      <ErrorResetContext.Provider value="/a">
        <ErrorBoundary resetKey="fixed">
          <Boom error={new Error("kaput")} />
        </ErrorBoundary>
      </ErrorResetContext.Provider>,
    );
    rerender(
      <ErrorResetContext.Provider value="/b">
        <ErrorBoundary resetKey="fixed">
          <Boom error={null} />
        </ErrorBoundary>
      </ErrorResetContext.Provider>,
    );
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
  });
});
