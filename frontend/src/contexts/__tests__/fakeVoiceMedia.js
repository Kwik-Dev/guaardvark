/**
 * Browser media stand-ins for voice session tests: getUserMedia, an
 * AudioContext with a VAD worklet node, and MediaRecorder. Installed on the
 * globals the engine's default seams read, so tests exercise that wiring too.
 * `frame(level)` plays one VAD worklet message.
 */
import { vi } from "vitest";

export function installFakeMedia({ secure = true } = {}) {
  const state = {
    streams: [],
    contexts: [],
    nodes: [],
    recorders: [],
    getUserMedia: vi.fn(),
    permissionError: null,
  };

  class FakeTrack {
    constructor() {
      this.readyState = "live";
      this.stopped = false;
      this.listeners = {};
    }
    stop() {
      this.stopped = true;
      this.readyState = "ended";
    }
    addEventListener(type, fn) {
      this.listeners[type] = fn;
    }
  }

  class FakeStream {
    constructor() {
      this.track = new FakeTrack();
      state.streams.push(this);
    }
    getTracks() {
      return [this.track];
    }
    getAudioTracks() {
      return [this.track];
    }
  }

  state.getUserMedia.mockImplementation(async () => {
    if (state.permissionError) throw state.permissionError;
    return new FakeStream();
  });

  const node = () => ({ connect: vi.fn(), disconnect: vi.fn() });

  class FakeAudioContext {
    constructor() {
      this.state = "running";
      this.closed = false;
      this.destination = {};
      this.currentTime = 0;
      this.audioWorklet = { addModule: vi.fn(async () => {}) };
      state.contexts.push(this);
    }
    resume() {
      this.state = "running";
      return Promise.resolve();
    }
    close() {
      this.closed = true;
      this.state = "closed";
      return Promise.resolve();
    }
    createMediaStreamSource() {
      return node();
    }
    createGain() {
      return { ...node(), gain: { value: 1 } };
    }
  }

  class FakeAudioWorkletNode {
    constructor(ctx, name) {
      this.context = ctx;
      this.name = name;
      this.port = { onmessage: null };
      this.connect = vi.fn();
      this.disconnect = vi.fn();
      state.nodes.push(this);
    }
  }

  class FakeMediaRecorder {
    constructor(stream, options) {
      this.stream = stream;
      this.mimeType = options?.mimeType || "audio/webm";
      this.state = "inactive";
      this.ondataavailable = null;
      this.onstop = null;
      state.recorders.push(this);
    }
    static isTypeSupported(type) {
      return type === "audio/webm;codecs=opus";
    }
    start(timeslice) {
      this.state = "recording";
      this.timeslice = timeslice;
    }
    stop() {
      if (this.state === "inactive") return;
      this.state = "inactive";
      // A real recorder hands over its last chunk, then fires stop.
      this.ondataavailable?.({ data: new Blob([new Uint8Array(4000)], { type: this.mimeType }) });
      Promise.resolve().then(() => this.onstop?.());
    }
  }

  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal("AudioWorkletNode", FakeAudioWorkletNode);
  vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
  vi.stubGlobal("isSecureContext", secure);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: secure ? { getUserMedia: state.getUserMedia } : undefined,
  });

  state.frame = (level) => {
    const current = state.nodes[state.nodes.length - 1];
    current?.port.onmessage?.({ data: { volume: level, isSpeaking: level > 0.03 } });
  };
  state.liveTracks = () => state.streams.filter((s) => !s.track.stopped).length;
  state.activeRecorders = () => state.recorders.filter((r) => r.state === "recording").length;
  state.uninstall = () => {
    vi.unstubAllGlobals();
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: undefined });
  };
  return state;
}

/**
 * A JSON fetch response for the speech-to-text route. A plain object rather
 * than Response: its body reading needs no timers, which the tests fake.
 */
export function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    url: "/api/voice/speech-to-text",
    headers: { get: (name) => (name.toLowerCase() === "content-type" ? "application/json" : null) },
    text: async () => JSON.stringify(body),
  };
}
