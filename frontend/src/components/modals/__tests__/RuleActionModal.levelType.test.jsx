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

  it("keeps a SYSTEM prompt at SYSTEM even when its name is renamed", async () => {
    const onSave = vi.fn();
    render(
      <RuleActionModal
        open
        ruleData={{
          id: 2,
          name: "global_default_chat_system_prompt",
          level: "SYSTEM",
          type: "PROMPT_TEMPLATE",
          rule_text: "You are Guaardvark.",
        }}
        onSave={onSave}
        onClose={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText(/^Name/), { target: { name: "name", value: "persona" } });
    fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave.mock.calls[0][0]).toMatchObject({ name: "persona", level: "SYSTEM" });
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

  it("creates the global chat prompt at SYSTEM", () => {
    expect(effectiveTypeAndLevel({ name: "global_default_chat_system_prompt" }, null)).toEqual({
      type: "PROMPT_TEMPLATE",
      level: "SYSTEM",
    });
  });
});
