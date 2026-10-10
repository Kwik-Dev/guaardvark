import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../../../api", () => ({
  getAvailableModels: vi.fn(async () => [{ name: "gemma4:12b" }]),
}));

import RuleActionModal, { contentFieldFor, effectiveTypeAndLevel } from "../RuleActionModal";

beforeEach(() => {
  vi.clearAllMocks();
});

const saveFrom = async (ruleData) => {
  const onSave = vi.fn();
  render(<RuleActionModal open ruleData={ruleData} onSave={onSave} onClose={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));
  await waitFor(() => expect(onSave).toHaveBeenCalled());
  return onSave.mock.calls[0][0];
};

describe("RuleActionModal keeps the stored level and type on edit", () => {
  it("saves qa_default as the QA_TEMPLATE / SYSTEM row the chat reads", async () => {
    const payload = await saveFrom({
      id: 4,
      name: "qa_default",
      level: "SYSTEM",
      type: "QA_TEMPLATE",
      rule_text: "Answer from context.",
    });
    expect(payload).toMatchObject({ id: 4, level: "SYSTEM", type: "QA_TEMPLATE" });
  });

  it("does not demote a LEARNED row to USER_GLOBAL", async () => {
    const payload = await saveFrom({
      id: 23,
      name: "brevity_on_small_models",
      level: "LEARNED",
      type: "PROMPT_TEMPLATE",
      rule_text: "Answer in at most three sentences.",
    });
    expect(payload).toMatchObject({ level: "LEARNED", type: "PROMPT_TEMPLATE" });
  });

  it("keeps the level of a row whose type is null and leaves type out of the save", async () => {
    const payload = await saveFrom({ id: 5, name: "house_style", level: "SYSTEM", type: null, rule_text: "t" });
    expect(payload.level).toBe("SYSTEM");
    expect("type" in payload).toBe(false);
  });

  it.each(["global_default_chat_system_prompt", "enhanced_chat", "qa_default"])(
    "makes the name of %s read-only because the engine looks it up by name",
    (name) => {
      render(
        <RuleActionModal
          open
          ruleData={{ id: 2, name, level: "SYSTEM", type: "PROMPT_TEMPLATE", rule_text: "x" }}
          onSave={vi.fn()}
          onClose={() => {}}
        />,
      );
      expect(screen.getByLabelText(/^Name/)).toBeDisabled();
      expect(screen.getByText(/looks this rule up by name/)).toBeInTheDocument();
    },
  );

  it("keeps the type fixed after creation: a prompt rule cannot take a command label", () => {
    render(
      <RuleActionModal
        open
        ruleData={{ id: 7, name: "tone", level: "USER_GLOBAL", type: "PROMPT_TEMPLATE", rule_text: "x" }}
        onSave={vi.fn()}
        onClose={() => {}}
      />,
    );
    expect(screen.getByLabelText(/Command \(optional\)/)).toBeDisabled();
    expect(screen.getByText(/Type is fixed after creation/)).toBeInTheDocument();
  });

  it("keeps the type fixed after creation: a command rule must keep a label", () => {
    render(
      <RuleActionModal
        open
        ruleData={{ id: 8, name: "go", level: "USER_GLOBAL", type: "COMMAND_RULE", command_label: "/go", rule_text: "x" }}
        onSave={vi.fn()}
        onClose={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText(/Command \(optional\)/), { target: { name: "command_label", value: "" } });
    expect(screen.getByRole("button", { name: /^Save$/ })).toBeDisabled();
    expect(screen.getByText(/Required; the type is fixed after creation/)).toBeInTheDocument();
  });

  it("shows the stored pair so the user knows what the save keeps", () => {
    render(
      <RuleActionModal
        open
        ruleData={{ id: 23, name: "x", level: "LEARNED", type: "PROMPT_TEMPLATE", rule_text: "y" }}
        onSave={vi.fn()}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText(/LEARNED \/ PROMPT_TEMPLATE, kept on save/)).toBeInTheDocument();
  });
});

describe("the content field is labelled by the rule's type", () => {
  it("names the field after the type the save will store", () => {
    expect(contentFieldFor("PROMPT_TEMPLATE").label).toBe("Prompt Template");
    expect(contentFieldFor("QA_TEMPLATE").label).toBe("QA Template");
    expect(contentFieldFor("COMMAND_RULE").label).toBe("Command Prompt");
    expect(contentFieldFor("FILTER_RULE").label).toBe("Rule Content");
  });

  it("shows Prompt Template for a stored prompt template", () => {
    render(
      <RuleActionModal
        open
        ruleData={{ id: 2, name: "persona", level: "SYSTEM", type: "PROMPT_TEMPLATE", rule_text: "y" }}
        onSave={vi.fn()}
        onClose={() => {}}
      />,
    );
    expect(screen.getByLabelText(/Prompt Template/)).toHaveValue("y");
  });

  it("switches to Command Prompt while a new rule gets a command label", () => {
    render(<RuleActionModal open ruleData={null} onSave={vi.fn()} onClose={() => {}} />);
    expect(screen.getByLabelText(/Prompt Template/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Command \(optional\)/), {
      target: { name: "command_label", value: "/brief" },
    });
    expect(screen.getByLabelText(/Command Prompt/)).toBeInTheDocument();
  });
});

describe("Target Models when the installed model list is unavailable", () => {
  it("offers only All Models and says why, never a hosted model name", async () => {
    const api = await import("../../../api");
    api.getAvailableModels.mockResolvedValueOnce({ error: "Ollama unreachable" });
    render(
      <RuleActionModal
        open
        ruleData={{ id: 9, name: "r", level: "USER_GLOBAL", type: "PROMPT_TEMPLATE", rule_text: "t", target_models: ["gemma4:12b"] }}
        onSave={vi.fn()}
        onClose={() => {}}
      />,
    );
    await screen.findByText(/Could not load the installed model list: Ollama unreachable/);
    fireEvent.mouseDown(screen.getByLabelText(/Target Models/));
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["All Models"]);
    expect(screen.queryByText(/gpt-4|gpt-3\.5|claude-2/)).toBeNull();
    // The target already stored on the rule is still shown and will be saved.
    expect(screen.getByText("gemma4:12b")).toBeInTheDocument();
  });

  it("says so when the list comes back empty", async () => {
    const api = await import("../../../api");
    api.getAvailableModels.mockResolvedValueOnce([]);
    render(<RuleActionModal open ruleData={null} onSave={vi.fn()} onClose={() => {}} />);
    await screen.findByText(/No installed models were listed/);
  });
});

describe("a new rule derives its level and type from the form", () => {
  it("is a USER_GLOBAL prompt template by default", () => {
    expect(effectiveTypeAndLevel({ name: "tone" }, null)).toEqual({
      type: "PROMPT_TEMPLATE",
      level: "USER_GLOBAL",
    });
  });

  it("is a command rule when it has a command label", () => {
    expect(effectiveTypeAndLevel({ name: "x", command_label: "/go" }, null)).toEqual({
      type: "COMMAND_RULE",
      level: "USER_GLOBAL",
    });
  });

  it("creates qa_default as the QA_TEMPLATE row rule_utils looks up", () => {
    expect(effectiveTypeAndLevel({ name: "qa_default" }, null)).toEqual({
      type: "QA_TEMPLATE",
      level: "SYSTEM",
    });
  });

  it.each(["global_default_chat_system_prompt", "enhanced_chat"])(
    "creates %s at SYSTEM, where the prompt-slot lookup reads it",
    (name) => {
      expect(effectiveTypeAndLevel({ name }, null)).toEqual({ type: "PROMPT_TEMPLATE", level: "SYSTEM" });
    },
  );
});
