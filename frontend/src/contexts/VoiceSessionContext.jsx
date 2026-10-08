import React, { createContext, useContext, useEffect, useMemo, useRef } from "react";
import { useVoice } from "./VoiceContext";
import { useAppStore } from "../stores/useAppStore";
import { useVoiceSessionStore } from "../stores/useVoiceSessionStore";
import { useVoiceSettings } from "../hooks/useVoiceSettings";
import brand from "../config/brand";
import VoiceSessionEngine from "./voiceSessionEngine";

const VoiceSessionContext = createContext(null);

// Holding the shortcut this long turns it into push-to-talk.
const HOLD_MS = 300;

/** Ctrl+Shift+Space, the one keyboard shortcut for the mic. Bare Space is never taken. */
export const isVoiceShortcut = (event) =>
  event.code === "Space" && event.ctrlKey && event.shiftKey && !event.altKey && !event.metaKey;

const NOOP_SESSION = Object.freeze({
  available: false,
  toggle: () => Promise.resolve(false),
  toggleHandsFree: () => Promise.resolve(false),
  start: () => Promise.resolve(false),
  stop: () => {},
  pushStart: () => Promise.resolve(false),
  pushEnd: () => {},
  acquire: () => Promise.resolve(false),
  release: () => {},
  registerSink: () => () => {},
  replyFinished: () => {},
  stopSpeaking: () => {},
  clearError: () => {},
});

/**
 * Global voice session: one microphone for the whole app.
 *
 * Mounted inside VoiceProvider (it follows VoiceContext.isPlaying to keep the
 * mic shut while a reply is spoken). Never opens the mic or asks for
 * permission by itself; the first mic click or shortcut does.
 *
 * @param {{children: React.ReactNode, deps?: object}} props
 *   `deps` replaces the engine's browser and network seams (tests).
 */
export function VoiceSessionProvider({ children, deps }) {
  const engineRef = useRef(null);
  if (!engineRef.current) {
    engineRef.current = new VoiceSessionEngine({ store: useVoiceSessionStore, deps });
  }
  const engine = engineRef.current;

  const voice = useVoice();
  const isPlaying = Boolean(voice?.isPlaying);
  const settings = useVoiceSettings();
  const activationMode = useAppStore((s) => s.voiceActivationMode);
  const systemName = useAppStore((s) => s.systemName) || brand.appName;

  useEffect(() => {
    engine.attach();
    return () => engine.shutdown();
  }, [engine]);

  useEffect(() => {
    engine.configure({ settings });
  }, [engine, settings]);

  useEffect(() => {
    engine.configure({ activationMode });
  }, [engine, activationMode]);

  useEffect(() => {
    engine.configure({ systemName });
  }, [engine, systemName]);

  useEffect(() => {
    engine.setPlaybackActive(isPlaying);
  }, [engine, isPlaying]);

  // Ctrl+Shift+Space: a tap does what a mic click does, a hold talks.
  useEffect(() => {
    let pressed = false;
    let pushing = false;
    let holdTimer = null;

    const endPress = () => {
      clearTimeout(holdTimer);
      holdTimer = null;
      pressed = false;
      if (pushing) {
        pushing = false;
        engine.pushEnd();
        return true;
      }
      return false;
    };

    const onKeyDown = (event) => {
      if (!isVoiceShortcut(event)) return;
      event.preventDefault();
      if (event.repeat || pressed) return;
      pressed = true;
      holdTimer = setTimeout(() => {
        holdTimer = null;
        pushing = true;
        engine.pushStart();
      }, HOLD_MS);
    };
    // Space may come up after Ctrl or Shift, so the release is matched on Space alone.
    const onKeyUp = (event) => {
      if (!pressed || event.code !== "Space") return;
      event.preventDefault();
      if (!endPress()) engine.toggle();
    };
    const onBlur = () => {
      if (pressed) endPress();
    };

    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("keyup", onKeyUp, true);
      window.removeEventListener("blur", onBlur);
      clearTimeout(holdTimer);
    };
  }, [engine]);

  const actions = useMemo(
    () => ({
      available: true,
      toggle: () => engine.toggle(),
      // The /voice command: hands-free on or off whatever the click mode is.
      toggleHandsFree: () =>
        engine.session || engine.pushActive ? engine.stop() : engine.start("handsfree"),
      start: (kind) => engine.start(kind),
      stop: (options) => engine.stop(options),
      pushStart: () => engine.pushStart(),
      pushEnd: () => engine.pushEnd(),
      acquire: (reason) => engine.acquire(reason),
      release: (reason) => engine.release(reason),
      registerSink: (sink) => engine.registerSink(sink),
      replyFinished: () => engine.replyFinished(),
      stopSpeaking: () => engine.stopSpeaking(),
      clearError: () => engine.clearError(),
    }),
    [engine]
  );

  return <VoiceSessionContext.Provider value={actions}>{children}</VoiceSessionContext.Provider>;
}

/** Actions of the global voice session (no-ops outside the provider). */
export function useVoiceSession() {
  return useContext(VoiceSessionContext) || NOOP_SESSION;
}

/** Select from the live voice session state, e.g. useVoiceSessionState((s) => s.phase). */
export const useVoiceSessionState = (selector) => useVoiceSessionStore(selector);

export default VoiceSessionProvider;
