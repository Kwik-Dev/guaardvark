import React, { useEffect, useState } from "react";
import { Box, FormControlLabel, Switch, Tooltip, Typography } from "@mui/material";
import { getOllamaLifecycle, setOllamaLifecycle } from "../../api/settingsService";
import { useSnackbar } from "../common/SnackbarProvider";

/**
 * Two switches that decide whether the start/stop scripts touch Ollama.
 * Both persist to .env and apply on the next stop or start; nothing restarts.
 */
// `title` lets the host name the block: "Ollama" where it stands alone, "Lifecycle"
// when it already sits inside the Ollama plugin card.
const OllamaLifecycleSection = ({ title = "Ollama" }) => {
  const { showMessage } = useSnackbar();
  const [state, setState] = useState({ keep_running: false, external: false, env_writable: true });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getOllamaLifecycle()
      .then((data) => {
        const payload = data?.data ?? data;
        if (!cancelled && payload && typeof payload === "object") setState((s) => ({ ...s, ...payload }));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const update = async (patch) => {
    setSaving(true);
    try {
      const data = await setOllamaLifecycle(patch);
      const payload = data?.data ?? data;
      setState((s) => ({ ...s, ...patch, ...(payload || {}) }));
      showMessage("Saved to .env; applies on the next stop or start", "success");
    } catch (err) {
      showMessage("Could not save: " + (err.message || err), "error");
    } finally {
      setSaving(false);
    }
  };

  const disabled = saving || state.env_writable === false;
  const external = Boolean(state.external);

  // Wrapped in a span only while "external" wins, because a disabled MUI Switch
  // swallows the pointer events a Tooltip listens for.
  const keepRunningSwitch = (
    <Switch
      size="small"
      checked={Boolean(state.keep_running)}
      disabled={disabled || external}
      onChange={(e) => update({ keep_running: e.target.checked })}
      inputProps={{ "data-testid": "ollama-keep-running" }}
    />
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5, mt: 2 }} data-testid="ollama-lifecycle">
      <Typography variant="subtitle2">{title}</Typography>
      <FormControlLabel
        control={
          external ? (
            <Tooltip title="Not applicable while Ollama is external" arrow>
              <span>{keepRunningSwitch}</span>
            </Tooltip>
          ) : (
            keepRunningSwitch
          )
        }
        label={<Typography variant="body2">Leave Ollama running when Guaardvark stops</Typography>}
      />
      <FormControlLabel
        control={
          <Switch
            size="small"
            checked={external}
            disabled={disabled}
            onChange={(e) => update({ external: e.target.checked })}
            inputProps={{ "data-testid": "ollama-external" }}
          />
        }
        label={<Typography variant="body2">I run Ollama myself: never start or stop it</Typography>}
      />
      <Typography variant="caption" color="text.secondary">
        Both apply on the next stop or start — nothing restarts now. By default stop.sh
        stops only the Ollama that start.sh launched. These write
        GUAARDVARK_OLLAMA_KEEP_RUNNING / GUAARDVARK_OLLAMA_EXTERNAL to .env.
        {state.env_writable === false ? " (.env is not writable by the server; set them by hand.)" : ""}
      </Typography>
    </Box>
  );
};

export default OllamaLifecycleSection;
