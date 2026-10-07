import React from "react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render } from "@testing-library/react";

let sink = null;
const replyFinished = vi.fn();
vi.mock("../../contexts/VoiceSessionContext", () => ({
  useVoiceSession: () => session,
}));
const session = {
  registerSink: (s) => {
    sink = s;
    return () => {
      sink = null;
    };
  },
  replyFinished: (...args) => replyFinished(...args),
};

import useVoiceSink from "../useVoiceSink";

function Chat({ busy, send }) {
  useVoiceSink({ id: "chat-page", priority: 10, busy, send });
  return null;
}

beforeEach(() => {
  vi.useFakeTimers();
  sink = null;
  replyFinished.mockClear();
});
afterEach(() => vi.useRealTimers());

describe("useVoiceSink", () => {
  it("registers while mounted and sends straight away when the chat is free", async () => {
    const send = vi.fn();
    const view = render(<Chat busy={false} send={send} />);
    expect(sink).toMatchObject({ id: "chat-page", priority: 10 });
    await act(async () => {
      sink.deliver({ id: "t1", text: "hello" });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(send).toHaveBeenCalledWith("hello", expect.objectContaining({ id: "t1" }));
    view.unmount();
    expect(sink).toBe(null);
  });

  it("holds a turn while a reply streams and sends it, in order, when it ends", async () => {
    const sent = [];
    let currentSession = "session_1";
    const send = vi.fn((text) => sent.push([text, currentSession]));
    const view = render(<Chat busy send={send} />);

    await act(async () => {
      sink.deliver({ id: "a", text: "first" });
      sink.deliver({ id: "b", text: "second" });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(send).not.toHaveBeenCalled();

    currentSession = "session_2";
    view.rerender(<Chat busy={false} send={send} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(sent).toEqual([["first", "session_2"]]);

    // The send makes the chat busy; when that reply ends the next turn goes.
    view.rerender(<Chat busy send={send} />);
    view.rerender(<Chat busy={false} send={send} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(sent.map(([t]) => t)).toEqual(["first", "second"]);
    expect(replyFinished).toHaveBeenCalledTimes(1);
  });

  it("moves on if a send never makes the chat busy", async () => {
    const send = vi.fn();
    render(<Chat busy={false} send={send} />);
    await act(async () => {
      sink.deliver({ id: "a", text: "dropped as a duplicate" });
      sink.deliver({ id: "b", text: "next" });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(send).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    expect(send).toHaveBeenCalledTimes(2);
    expect(replyFinished).toHaveBeenCalled();
  });
});
