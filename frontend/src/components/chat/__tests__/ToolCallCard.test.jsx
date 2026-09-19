import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import ToolCallCard from "../ToolCallCard";
import { consentImageSrc, parseConsentApproval } from "../consentApproval";

const renderCard = (props) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <ToolCallCard toolName="run_command" isPending requiresApproval {...props} />
    </ThemeProvider>,
  );

describe("parseConsentApproval", () => {
  it("reads consent, image and prompt from the approval payload", () => {
    const parsed = parseConsentApproval(
      {
        consent: true,
        image: "/api/outputs/generated_images/face.png",
        prompt: "this person as a 1940s detective",
        tools: ["generate_identity"],
      },
      "generate_identity",
      {},
    );
    expect(parsed.consent).toBe(true);
    expect(parsed.image).toBe("/api/outputs/generated_images/face.png");
    expect(parsed.prompt).toBe("this person as a 1940s detective");
  });

  it("falls back to tool_details.params when the top-level keys are absent", () => {
    const parsed = parseConsentApproval(
      {
        tools: ["generate_identity"],
        tool_details: [{
          tool: "generate_identity",
          consent: true,
          params: { image: "/uploads/ref.jpg", prompt: "a rainy street" },
        }],
      },
      "generate_identity",
      {},
    );
    expect(parsed.consent).toBe(true);
    expect(parsed.image).toBe("/uploads/ref.jpg");
    expect(parsed.prompt).toBe("a rainy street");
  });

  it("is not a consent request when the flag is missing", () => {
    const parsed = parseConsentApproval(
      { tools: ["run_command"], tool_details: [{ tool: "run_command", params: { cmd: "ls" } }] },
      "run_command",
      { cmd: "ls" },
    );
    expect(parsed.consent).toBe(false);
  });
});

describe("consentImageSrc", () => {
  it("passes through served URLs and maps generated_images paths", () => {
    expect(consentImageSrc("/api/outputs/generated_images/a.png")).toBe("/api/outputs/generated_images/a.png");
    expect(consentImageSrc("/home/box/data/outputs/generated_images/a.png")).toBe("/api/outputs/generated_images/a.png");
    expect(consentImageSrc("/tmp/secret.png")).toBeNull();
  });
});

describe("ToolCallCard approval variants", () => {
  it("renders the generic approve/reject card when consent is not set", () => {
    renderCard({ params: { cmd: "ls" } });
    expect(screen.getByTestId("tool-approval-card")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
    expect(screen.queryByTestId("consent-approval-card")).not.toBeInTheDocument();
    expect(screen.queryByText(/right to use this person's likeness/i)).not.toBeInTheDocument();
  });

  it("renders the consent card with likeness copy, thumbnail, prompt, and filled approve", () => {
    const onApproval = vi.fn();
    renderCard({
      toolName: "generate_identity",
      consent: true,
      consentImage: "/api/outputs/generated_images/face.png",
      consentPrompt: "this person as a 1940s detective",
      onApproval,
    });

    expect(screen.getByTestId("consent-approval-card")).toBeInTheDocument();
    expect(screen.getByText(/right to use this person's likeness/i)).toBeInTheDocument();
    expect(screen.getByText("this person as a 1940s detective")).toBeInTheDocument();
    const thumb = screen.getByAltText("Reference likeness");
    expect(thumb).toHaveAttribute("src", "/api/outputs/generated_images/face.png");
    expect(screen.getByRole("button", { name: "I have the right to use this likeness" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Decline" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "I have the right to use this likeness" }));
    expect(onApproval).toHaveBeenCalledWith(true);
  });

  it("sends the same boolean decline the generic card sends", () => {
    const onApproval = vi.fn();
    renderCard({
      toolName: "generate_identity",
      consent: true,
      consentPrompt: "a rainy street",
      onApproval,
    });
    fireEvent.click(screen.getByRole("button", { name: "Decline" }));
    expect(onApproval).toHaveBeenCalledWith(false);
  });
});
