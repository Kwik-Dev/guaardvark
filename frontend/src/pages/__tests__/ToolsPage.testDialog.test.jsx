import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";

const executeTool = vi.fn(async () => ({ success: true, result: { success: true, output: "ran" } }));

vi.mock("../../api/toolsService", () => ({
  getTools: vi.fn(async () => ({
    success: true,
    count: 2,
    tools: [
      {
        name: "system_command",
        description: "Run a shell command",
        requires_approval: true,
        parameters: {
          command: { type: "string", required: true, description: "shell line", default: null },
          timeout: { type: "int", required: false, description: "seconds", default: 30 },
        },
      },
      {
        name: "media_status",
        description: "What is playing",
        requires_approval: false,
        parameters: {},
      },
    ],
  })),
  executeTool: (...args) => executeTool(...args),
  getToolSchemas: vi.fn(async () => ({ success: true, schemas: "" })),
  getToolCategories: vi.fn(async () => ({
    success: true,
    categories: { system: ["system_command"], media: ["media_status"] },
  })),
}));
vi.mock("../../contexts/StatusContext", () => ({
  useStatus: () => ({ activeModel: "m", isLoadingModel: false, modelError: null }),
}));
vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children, actions }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
vi.mock("../../components/common/LoadingStates", () => ({
  ContextualLoader: () => <div>loading</div>,
}));
vi.mock("../../components/common/ApiKeyRefusalNotice", () => ({
  ApiKeyRefusalAlert: ({ message }) => <div>{message}</div>,
}));

import ToolsPage from "../ToolsPage";

const WARNING = /runs this tool for real, right away, without the approval step/;

beforeEach(() => {
  executeTool.mockClear();
  localStorage.clear();
});

const openTestFor = async (toolName) => {
  render(<ToolsPage />);
  const title = await screen.findByText(toolName);
  const card = title.closest(".MuiCard-root");
  fireEvent.click(within(card).getByRole("button", { name: /Test/ }));
  return screen.findByRole("dialog");
};

describe("ToolsPage test dialog", () => {
  it("says an approval-gated tool runs without chat's approval step and waits for required fields", async () => {
    const dialog = await openTestFor("system_command");
    expect(within(dialog).getByText(WARNING)).toBeInTheDocument();

    const execute = within(dialog).getByRole("button", { name: /Execute/ });
    expect(execute).toBeDisabled();
    expect(within(dialog).getByText(/Required\. shell line/)).toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText(/command \*/), { target: { value: "ls" } });
    expect(within(dialog).queryByText(/Required\. shell line/)).toBeNull();
    expect(execute).not.toBeDisabled();

    fireEvent.click(execute);
    await waitFor(() => expect(executeTool).toHaveBeenCalledTimes(1));
    expect(executeTool).toHaveBeenCalledWith("system_command", { command: "ls", timeout: 30 });
  });

  it("shows no warning for a tool chat runs without asking, and lets it run at once", async () => {
    const dialog = await openTestFor("media_status");
    expect(within(dialog).queryByText(WARNING)).toBeNull();
    expect(within(dialog).getByRole("button", { name: /Execute/ })).not.toBeDisabled();
  });

  it("labels a tool with the category the registry gave it", async () => {
    render(<ToolsPage />);
    const title = await screen.findByText("media_status");
    expect(within(title.closest(".MuiCard-root")).getByText("media")).toBeInTheDocument();
  });
});
