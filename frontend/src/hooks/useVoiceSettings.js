import { useState, useEffect } from 'react';
import {
  VOICE_SETTINGS_EVENT,
  VOICE_SETTINGS_KEY,
  readVoiceSettings,
} from '../config/voiceDefaults';

/**
 * Voice settings with defaults filled in, re-read whenever a writer in this
 * tab (VOICE_SETTINGS_EVENT) or another tab (storage) changes them.
 */
export function useVoiceSettings() {
  const [settings, setSettings] = useState(readVoiceSettings);

  useEffect(() => {
    const handleChange = () => setSettings(readVoiceSettings());
    const handleStorage = (e) => {
      if (!e.key || e.key === VOICE_SETTINGS_KEY) handleChange();
    };
    window.addEventListener(VOICE_SETTINGS_EVENT, handleChange);
    window.addEventListener('storage', handleStorage);
    return () => {
      window.removeEventListener(VOICE_SETTINGS_EVENT, handleChange);
      window.removeEventListener('storage', handleStorage);
    };
  }, []);

  return settings;
}

export default useVoiceSettings;
