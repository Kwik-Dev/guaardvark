// The Whisper model voice transcribes with, chosen on this machine. Used by
// Settings → Voice and the Voice page. Lists installed models only: choosing
// never downloads, and installs happen in Voice models.
import React, { useCallback, useEffect, useState } from "react";
import PropTypes from "prop-types";
import { FormControl, InputLabel, MenuItem, Select } from "@mui/material";
import voiceService from "../../api/voiceService";
import { ActionButton, Cluster, Hint, Line } from "../settings/ui";

const HELP =
  "The Whisper model that turns what you say into text, on this machine. Larger models are more accurate and take longer per message. Only installed models are listed; install others in Voice models (Settings → Voice).";

/**
 * @param {{reloadKey?: any, onManageModels?: () => void, onChange?: (id: string) => void,
 *   showMessage?: (text: string, severity: string) => void}} props
 *   `reloadKey` re-reads the list when it changes (after an install, say);
 *   `onManageModels` adds a button that opens Voice models.
 */
const SpeechModelSelect = ({ reloadKey, onManageModels, onChange, showMessage }) => {
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await voiceService.getSpeechModel();
      setState(res?.data || null);
      setError(null);
    } catch (e) {
      setError(e?.message || "Could not read the speech model");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, reloadKey]);

  const installed = state?.installed || [];
  const nameOf = (id) => installed.find((m) => m.id === id)?.name || id;

  const choose = async (modelId) => {
    if (!modelId) return;
    setSaving(true);
    try {
      const res = await voiceService.setSpeechModel(modelId);
      setState(res?.data || null);
      setError(null);
      showMessage?.(`Speech recognition now uses ${nameOf(modelId)}`, "success");
      onChange?.(modelId);
    } catch (e) {
      const text = `Could not change the speech model: ${e?.message || e}`;
      if (showMessage) showMessage(text, "error");
      else setError(text);
    } finally {
      setSaving(false);
    }
  };

  // The select shows what transcription uses: the choice, or tiny.en while
  // a chosen model is missing.
  const value = installed.some((m) => m.id === state?.in_use) ? state.in_use : "";
  const fallingBack = Boolean(state && state.model !== state.in_use);

  return (
    <Cluster label="Speech recognition model" help={HELP}>
      <Line>
        {installed.length > 0 ? (
          <FormControl size="small" className="grow">
            <InputLabel id="speech-model-label">Model</InputLabel>
            <Select
              labelId="speech-model-label"
              label="Model"
              value={value}
              onChange={(e) => choose(e.target.value)}
              disabled={saving}
            >
              {installed.map((m) => (
                <MenuItem key={m.id} value={m.id}>
                  {m.name}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        ) : (
          state && <Hint>No speech model is installed yet.</Hint>
        )}
        {onManageModels && (
          <ActionButton onClick={onManageModels} tooltip="Install Whisper and voice models">
            Voice models
          </ActionButton>
        )}
      </Line>
      {fallingBack && (
        <Hint>
          {state.model_name || state.model} is not installed, so {nameOf(state.in_use)} is used until it is.
        </Hint>
      )}
      {error && <Hint sx={{ color: "error.main" }}>{error}</Hint>}
    </Cluster>
  );
};

SpeechModelSelect.propTypes = {
  reloadKey: PropTypes.any,
  onManageModels: PropTypes.func,
  onChange: PropTypes.func,
  showMessage: PropTypes.func,
};

export default SpeechModelSelect;
