import React from "react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useVoiceSessionStore, INITIAL_VOICE_SESSION, derivePhase } from "../../../stores/useVoiceSessionStore";

const voice = {
  available: true,
  toggle: vi.fn(),
  start: vi.fn(),
  stop: vi.fn(),
  pushStart: vi.fn(),
  pushEnd: vi.fn(),
  clearError: vi.fn(),
  stopSpeaking: vi.fn(),
};
vi.mock("../../../contexts/VoiceSessionContext", async () => {
  const store = await import("../../../stores/useVoiceSessionStore");
  return {
    useVoiceSession: () => voice,
    useVoiceSessionState: (selector) => store.useVoiceSessionStore(selector),
  };
});
vi.mock("../../../contexts/VoiceContext", () => ({ useVoice: () => ({ availableVoices: [] }) }));

import GlobalMicButton from "../GlobalMicButton";

const setSession = (patch) =>
  act(() => {
    useVoiceSessionStore.setState((s) => {
      const next = { ...s, ...patch };
      return { ...next, phase: derivePhase(next) };
    });
  });

const renderButton = (props) =>
  render(
    <MemoryRouter>
      <GlobalMicButton variant="bar" {...props} />
    </MemoryRouter>
  );

const micButton = () => screen.getByRole("button", { name: /^Voice:/ });

beforeEach(() => {
  vi.useFakeTimers();
  Object.values(voice).forEach((fn) => typeof fn === "function" && fn.mockClear());
  localStorage.clear();
  useVoiceSessionStore.setState({ ...INITIAL_VOICE_SESSION });
});
afterEach(() => vi.useRealTimers());

describe("GlobalMicButton", () => {
  it("a click toggles the session", () => {
    renderButton();
    const button = micButton();
    fireEvent.pointerDown(button, { button: 0 });
    fireEvent.pointerUp(button, { button: 0 });
    fireEvent.click(button);
    expect(voice.toggle).toHaveBeenCalledTimes(1);
    expect(voice.pushStart).not.toHaveBeenCalled();
  });

  it("press and hold talks, release sends", () => {
    renderButton();
    const button = micButton();
    fireEvent.pointerDown(button, { button: 0 });
    act(() => {
      vi.advanceTimersByTime(350);
    });
    expect(voice.pushStart).toHaveBeenCalledTimes(1);
    fireEvent.pointerUp(button, { button: 0 });
    fireEvent.click(button);
    expect(voice.pushEnd).toHaveBeenCalledTimes(1);
    expect(voice.toggle).not.toHaveBeenCalled();
  });

  it("keyboard activation (Enter) is a click", () => {
    renderButton();
    fireEvent.click(micButton());
    expect(voice.toggle).toHaveBeenCalledTimes(1);
  });

  it("shows the session's state", async () => {
    renderButton();
    expect(micButton()).toHaveAttribute("data-voice-phase", "idle");
    await setSession({ session: "handsfree", micOpen: true });
    expect(micButton()).toHaveAttribute("data-voice-phase", "listening");
    expect(micButton()).toHaveAttribute("aria-pressed", "true");
    await setSession({ pendingStt: 1 });
    expect(micButton()).toHaveAttribute("data-voice-phase", "transcribing");
    await setSession({ pendingStt: 0, speaking: true });
    expect(micButton()).toHaveAttribute("data-voice-phase", "speaking");
  });

  it("the top-bar button opens its popover with the reason when the mic is blocked", async () => {
    renderButton({ primary: true });
    await setSession({ error: "Microphone access is blocked.", errorCode: "denied" });
    expect(screen.getByRole("alert")).toHaveTextContent("Microphone access is blocked.");
    // The open popover hides the page behind it from the accessibility tree.
    expect(screen.getByRole("button", { name: /^Voice:/, hidden: true })).toHaveAttribute(
      "data-voice-phase",
      "denied"
    );
  });

  it("is hidden when Settings turns voice chat off", () => {
    localStorage.setItem("guaardvark_voiceChatEnabled", "false");
    renderButton();
    expect(screen.queryByRole("button", { name: /^Voice:/ })).toBe(null);
  });
});
