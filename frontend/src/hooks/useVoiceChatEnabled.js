import { useEffect, useState } from "react";

export const VOICE_CHAT_ENABLED_KEY = "guaardvark_voiceChatEnabled";
export const VOICE_CHAT_ENABLED_EVENT = "voiceChatEnabledChanged";

const read = () => {
  try {
    return localStorage.getItem(VOICE_CHAT_ENABLED_KEY) !== "false";
  } catch {
    return true;
  }
};

/**
 * The Settings "Voice chat" switch: when off, the mic buttons are not shown.
 * On unless this browser turned it off.
 */
export function useVoiceChatEnabled() {
  const [enabled, setEnabled] = useState(read);
  useEffect(() => {
    const update = () => setEnabled(read());
    const onStorage = (e) => {
      if (!e.key || e.key === VOICE_CHAT_ENABLED_KEY) update();
    };
    window.addEventListener(VOICE_CHAT_ENABLED_EVENT, update);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(VOICE_CHAT_ENABLED_EVENT, update);
      window.removeEventListener("storage", onStorage);
    };
  }, []);
  return enabled;
}

export default useVoiceChatEnabled;
