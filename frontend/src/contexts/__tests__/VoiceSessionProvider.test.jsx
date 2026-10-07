import React from "react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, fireEvent, render } from "@testing-library/react";
import { installFakeMedia, jsonResponse } from "./fakeVoiceMedia";
import { useVoiceSessionStore, INITIAL_VOICE_SESSION } from "../../stores/useVoiceSessionStore";
import { useAppStore } from "../../stores/useAppStore";
import { VOICE_SETTINGS_KEY } from "../../config/voiceDefaults";
import { VoiceSessionProvider, useVoiceSession } from "../VoiceSessionContext";

// VoiceContext needs the health and backend providers; the session only reads
// isPlaying from it, which each test sets here.
let voiceContextValue = { isPlaying: false };
vi.mock("../VoiceContext", () => ({ useVoice: () => voiceContextValue }));

const FRAME_MS = 30;
let media;
let actions;

function Harness({ sink, children }) {
  actions = useVoiceSession();
  React.useEffect(() => (sink ? actions.registerSink(sink) : undefined), [sink]);
  return children || null;
}

function renderSession(sink, children) {
  return render(
    <VoiceSessionProvider>
      <Harness sink={sink}>{children}</Harness>
    </VoiceSessionProvider>
  );
}

const settle = () => act(async () => {
  await vi.advanceTimersByTimeAsync(0);
});

/** Play `ms` of VAD frames at `level`, advancing the clock between frames. */
async function frames(level, ms) {
  await act(async () => {
    for (let t = 0; t < ms; t += FRAME_MS) {
      media.frame(level);
      await vi.advanceTimersByTimeAsync(FRAME_MS);
    }
  });
}

/** One utterance: quiet, speech, then a pause long enough to end it. */
async function utterance() {
  await frames(0.001, 300);
  await frames(0.12, 900);
  await frames(0.001, 2000);
  await settle();
}

const sttCalls = () => fetch.mock.calls.filter(([url]) => String(url).includes("/voice/speech-to-text"));

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  useVoiceSessionStore.setState({ ...INITIAL_VOICE_SESSION });
  useAppStore.setState({ voiceActivationMode: "handsfree", systemName: "Guaardvark" });
  voiceContextValue = { isPlaying: false };
  media = installFakeMedia();
  fetch.mockReset();
  fetch.mockImplementation(async () => jsonResponse({ text: "what is on my calendar" }));
});

afterEach(() => {
  media.uninstall();
  vi.useRealTimers();
});

describe("VoiceSessionProvider", () => {
  it("never opens the mic or asks for permission on its own", async () => {
    renderSession();
    await settle();
    expect(media.getUserMedia).not.toHaveBeenCalled();
    expect(useVoiceSessionStore.getState().phase).toBe("idle");
  });

  it("start → speech → pause → one STT request → sink gets the text", async () => {
    let sessionId = "floating_1";
    const delivered = [];
    renderSession({
      id: "test",
      // A sink reads its own session when the turn arrives.
      deliver: (turn) => delivered.push({ text: turn.text, sessionId }),
    });

    await act(async () => {
      await actions.start("handsfree");
    });
    expect(media.getUserMedia).toHaveBeenCalledTimes(1);
    expect(useVoiceSessionStore.getState().phase).toBe("listening");

    sessionId = "floating_2"; // "+ New chat" between starting and speaking
    await utterance();

    expect(sttCalls()).toHaveLength(1);
    const [url, init] = sttCalls()[0];
    expect(url).toBe("/api/voice/speech-to-text");
    expect(init.body.get("audio")).toBeInstanceOf(Blob);
    expect(delivered).toEqual([{ text: "what is on my calendar", sessionId: "floating_2" }]);
    const s = useVoiceSessionStore.getState();
    expect(s.lastTranscript).toMatchObject({ text: "what is on my calendar", outcome: "sent" });
    expect(s.phase).toBe("responding");
    expect(s.micOpen).toBe(true);
  });

  it("keeps listening in hands-free and sends each utterance in order", async () => {
    const texts = ["first thing", "second thing"];
    fetch.mockImplementation(async () => jsonResponse({ text: texts.shift() }));
    const delivered = [];
    renderSession({ id: "test", deliver: (turn) => delivered.push(turn.text) });
    await act(async () => {
      await actions.start("handsfree");
    });
    await utterance();
    await utterance();
    expect(delivered).toEqual(["first thing", "second thing"]);
    expect(media.getUserMedia).toHaveBeenCalledTimes(1);
  });

  it("stop sends the utterance in progress and releases the mic", async () => {
    const delivered = [];
    renderSession({ id: "test", deliver: (turn) => delivered.push(turn.text) });
    await act(async () => {
      await actions.start("handsfree");
    });
    await frames(0.12, 900); // mid-sentence, no pause yet
    await act(async () => {
      actions.stop();
    });
    await settle();

    expect(delivered).toEqual(["what is on my calendar"]);
    expect(media.liveTracks()).toBe(0);
    expect(media.activeRecorders()).toBe(0);
    expect(media.contexts.every((c) => c.closed)).toBe(true);
    expect(useVoiceSessionStore.getState().micOpen).toBe(false);
    expect(useVoiceSessionStore.getState().session).toBe(null);
  });

  it("a second start while the first is opening opens one stream", async () => {
    renderSession();
    await act(async () => {
      await Promise.all([actions.start("handsfree"), actions.start("handsfree"), actions.start("utterance")]);
    });
    expect(media.getUserMedia).toHaveBeenCalledTimes(1);
    expect(media.contexts).toHaveLength(1);
    expect(media.activeRecorders()).toBe(1);
  });

  it("stop during the permission wait leaves nothing open", async () => {
    let grant;
    media.getUserMedia.mockImplementationOnce(
      () => new Promise((resolve) => {
        grant = resolve;
      })
    );
    renderSession();
    let starting;
    await act(async () => {
      starting = actions.start("handsfree");
    });
    expect(useVoiceSessionStore.getState().phase).toBe("requesting-permission");
    await act(async () => {
      actions.stop();
    });
    const track = { stopped: false, stop() { this.stopped = true; }, addEventListener() {} };
    await act(async () => {
      grant({ getTracks: () => [track], getAudioTracks: () => [track] });
      await starting;
    });
    expect(track.stopped).toBe(true);
    expect(media.contexts).toHaveLength(0);
    expect(useVoiceSessionStore.getState().micOpen).toBe(false);
  });

  it("unmounting releases tracks, audio context and recorder", async () => {
    const view = renderSession({ id: "test", deliver: () => {} });
    await act(async () => {
      await actions.start("handsfree");
    });
    await frames(0.12, 300);
    view.unmount();
    await settle();
    expect(media.liveTracks()).toBe(0);
    expect(media.activeRecorders()).toBe(0);
    expect(media.contexts.every((c) => c.closed)).toBe(true);
    expect(useVoiceSessionStore.getState().micOpen).toBe(false);
  });

  it("does not transcribe while a reply is spoken, and listens again after", async () => {
    const delivered = [];
    const sink = { id: "test", deliver: (turn) => delivered.push(turn.text) };
    const view = renderSession(sink);
    await act(async () => {
      await actions.start("handsfree");
    });

    voiceContextValue = { isPlaying: true };
    view.rerender(
      <VoiceSessionProvider>
        <Harness sink={sink} />
      </VoiceSessionProvider>
    );
    expect(useVoiceSessionStore.getState().phase).toBe("speaking");
    await utterance(); // the speakers, picked up by the mic
    expect(sttCalls()).toHaveLength(0);
    expect(media.activeRecorders()).toBe(0);

    voiceContextValue = { isPlaying: false };
    view.rerender(
      <VoiceSessionProvider>
        <Harness sink={sink} />
      </VoiceSessionProvider>
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    expect(useVoiceSessionStore.getState().speaking).toBe(false);
    await utterance();
    expect(sttCalls()).toHaveLength(1);
    expect(delivered).toEqual(["what is on my calendar"]);
  });

  it("a blocked microphone shows as denied with the reason", async () => {
    media.permissionError = Object.assign(new Error("Permission denied"), { name: "NotAllowedError" });
    renderSession();
    await act(async () => {
      await actions.toggle();
    });
    const s = useVoiceSessionStore.getState();
    expect(s.phase).toBe("denied");
    expect(s.error).toMatch(/blocked/i);
    expect(s.session).toBe(null);
  });

  it("a missing speech model is an error that stops listening", async () => {
    fetch.mockImplementation(async () =>
      jsonResponse({ error: "Install the speech model to use voice", code: "SPEECH_MODEL_MISSING" }, 409)
    );
    renderSession({ id: "test", deliver: () => {} });
    await act(async () => {
      await actions.start("handsfree");
    });
    await utterance();
    const s = useVoiceSessionStore.getState();
    expect(s.phase).toBe("error");
    expect(s.error).toBe("Install the speech model to use voice");
    expect(media.liveTracks()).toBe(0);
  });

  it("silence the server could not transcribe is not an error", async () => {
    fetch.mockImplementation(async () => jsonResponse({ error: "No speech detected in audio" }, 400));
    renderSession({ id: "test", deliver: () => {} });
    await act(async () => {
      await actions.start("handsfree");
    });
    await utterance();
    const s = useVoiceSessionStore.getState();
    expect(s.error).toBe(null);
    expect(s.phase).toBe("listening");
  });

  it("an insecure page says so and never calls getUserMedia", async () => {
    media.uninstall();
    media = installFakeMedia({ secure: false });
    renderSession();
    await act(async () => {
      await actions.toggle();
    });
    expect(useVoiceSessionStore.getState().error).toBe("Mic needs HTTPS or localhost");
    expect(media.getUserMedia).not.toHaveBeenCalled();
  });

  it("with the wake phrase on, only speech after “Hey <name>” is sent", async () => {
    localStorage.setItem(VOICE_SETTINGS_KEY, JSON.stringify({ wakeWordEnabled: true }));
    useAppStore.setState({ systemName: "Jarvis" });
    const texts = ["turn on the lights", "Hey Jarvis, what time is it?", "and the date"];
    fetch.mockImplementation(async () => jsonResponse({ text: texts.shift() }));
    const delivered = [];
    renderSession({ id: "test", deliver: (turn) => delivered.push(turn.text) });
    await act(async () => {
      await actions.start("handsfree");
    });
    expect(useVoiceSessionStore.getState().listenMode).toBe("passive");
    await utterance();
    expect(delivered).toEqual([]);
    await utterance();
    expect(delivered).toEqual(["what time is it?"]);
    expect(useVoiceSessionStore.getState().listenMode).toBe("active");
    await utterance();
    expect(delivered).toEqual(["what time is it?", "and the date"]);
  });

  it("tap to talk sends one utterance and closes the mic", async () => {
    useAppStore.setState({ voiceActivationMode: "toggle" });
    const delivered = [];
    renderSession({ id: "test", deliver: (turn) => delivered.push(turn.text) });
    await act(async () => {
      await actions.toggle();
    });
    expect(useVoiceSessionStore.getState().session).toBe("utterance");
    await utterance();
    expect(delivered).toEqual(["what is on my calendar"]);
    expect(media.liveTracks()).toBe(0);
    expect(useVoiceSessionStore.getState().session).toBe(null);
  });
});

describe("/voice", () => {
  it("toggles hands-free listening through the provider, even in push-to-talk mode", async () => {
    useAppStore.setState({ voiceActivationMode: "push" });
    renderSession();
    const { executeBuiltinCommand } = await import("../../hooks/slashCommandHandlers");
    const addMessage = vi.fn();
    const run = (active) =>
      act(async () => {
        await executeBuiltinCommand("/voice", "", {
          addMessage,
          chatState: { voiceContext: { toggleVoice: actions.toggleHandsFree, isVoiceActive: active } },
          allCommands: [],
        });
        await vi.advanceTimersByTimeAsync(0);
      });
    await run(false);
    expect(useVoiceSessionStore.getState().session).toBe("handsfree");
    expect(addMessage).toHaveBeenLastCalledWith(expect.objectContaining({ content: "Voice chat enabled." }));
    await run(true);
    expect(useVoiceSessionStore.getState().session).toBe(null);
    expect(media.liveTracks()).toBe(0);
  });
});

describe("keyboard", () => {
  it("Space on a focused button does nothing to the mic", async () => {
    const { getByRole } = renderSession(null, <button type="button">Other</button>);
    const button = getByRole("button", { name: "Other" });
    button.focus();
    fireEvent.keyDown(button, { code: "Space", key: " " });
    fireEvent.keyUp(button, { code: "Space", key: " " });
    await settle();
    expect(media.getUserMedia).not.toHaveBeenCalled();
    expect(useVoiceSessionStore.getState().session).toBe(null);
  });

  it("Ctrl+Shift+Space toggles hands-free listening", async () => {
    renderSession();
    const press = async () => {
      fireEvent.keyDown(window, { code: "Space", key: " ", ctrlKey: true, shiftKey: true });
      fireEvent.keyUp(window, { code: "Space", key: " ", ctrlKey: true, shiftKey: true });
      await settle();
    };
    await press();
    expect(media.getUserMedia).toHaveBeenCalledTimes(1);
    expect(useVoiceSessionStore.getState().session).toBe("handsfree");
    await press();
    expect(useVoiceSessionStore.getState().session).toBe(null);
    expect(media.liveTracks()).toBe(0);
  });

  it("holding Ctrl+Shift+Space talks until it is released", async () => {
    const delivered = [];
    renderSession({ id: "test", deliver: (turn) => delivered.push(turn.text) });
    fireEvent.keyDown(window, { code: "Space", key: " ", ctrlKey: true, shiftKey: true });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });
    expect(useVoiceSessionStore.getState().phase).toBe("capturing");
    await frames(0.12, 1000);
    // Ctrl comes up first; releasing Space still ends the push.
    fireEvent.keyUp(window, { code: "Space", key: " " });
    await settle();
    expect(delivered).toEqual(["what is on my calendar"]);
    expect(media.liveTracks()).toBe(0);
  });
});
