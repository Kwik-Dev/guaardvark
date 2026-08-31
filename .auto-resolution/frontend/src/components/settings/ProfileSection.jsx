import React, { useEffect, useState } from "react";
import { Alert, FormControl, InputLabel, MenuItem, Select } from "@mui/material";
import { getProfile, setProfile as saveProfile } from "../../api/settingsService";
import { useAppStore } from "../../stores/useAppStore";
import { useSnackbar } from "../common/SnackbarProvider";
import { ActionButton, Cluster, Hint, Line } from "./ui";

/**
 * Product Profile — one switch that sets the product shape.
 *
 * Switching writes GUAARDVARK_PROFILE to .env; the new shape applies after a
 * restart, because env flags are read once at boot. Nothing is removed by a
 * profile: hidden pages stay reachable by URL, and this card can always put
 * them back.
 */
const ProfileSection = () => {
  const { showMessage } = useSnackbar();
  const activeProfile = useAppStore((s) => s.profile);
  const [info, setInfo] = useState(null);
  const [selected, setSelected] = useState("");
  const [saving, setSaving] = useState(false);
  const [restartNeeded, setRestartNeeded] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getProfile();
        if (cancelled) return;
        setInfo(data);
        setSelected(data?.configured || data?.active?.name || "workstation");
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const available = info?.available || [];
  const chosen = available.find((p) => p.name === selected);
  const activeName = info?.active?.name || activeProfile?.name;
  const changed = Boolean(info) && selected !== (info.configured || activeName);
  const locked = Boolean(info) && !info.env_writable;

  const handleApply = async () => {
    setSaving(true);
    try {
      await saveProfile(selected);
      setRestartNeeded(true);
      setInfo((prev) => (prev ? { ...prev, configured: selected } : prev));
      showMessage(`Profile set to ${chosen?.label || selected}. Restart to apply.`, "success");
    } catch (err) {
      showMessage(`Failed to set profile: ${err.message}`, "error");
    } finally {
      setSaving(false);
    }
  };

  if (error) {
    return <Alert severity="warning">Profiles unavailable: {error}</Alert>;
  }

  return (
    <Cluster label="Product profile" note={locked ? "set by .env" : "applies after a restart"}>
      <Line nowrap>
        <FormControl size="small" className="grow" disabled={!info || saving || locked}>
          <InputLabel id="product-profile-label">Profile</InputLabel>
          <Select
            labelId="product-profile-label"
            label="Profile"
            value={available.some((p) => p.name === selected) ? selected : ""}
            onChange={(e) => setSelected(e.target.value)}
          >
            {available.map((p) => (
              <MenuItem key={p.name} value={p.name}>
                {p.label}
                {p.source === "extension" ? " (extension)" : ""}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        {/* Primary only while a change is pending; otherwise nothing to commit. */}
        <ActionButton
          kind={changed ? "primary" : "neutral"}
          onClick={handleApply}
          loading={saving}
          disabled={!changed || locked}
          tooltip={locked ? "Set by GUAARDVARK_PROFILE in .env" : ""}
        >
          Apply
        </ActionButton>
      </Line>
      <Hint>
        {chosen?.description ? `${chosen.description} ` : ""}
        A profile decides what is listed and what is on by default. Nothing is removed: every page
        stays reachable by its address, and explicit settings in <code>.env</code> always win.
      </Hint>
      {locked && (
        <Alert severity="info" sx={{ py: 0.25 }}>
          The profile is set by <code>GUAARDVARK_PROFILE</code> in <code>.env</code>, which this server
          cannot write. Edit the file or start with <code>./start.sh --profile NAME</code>.
        </Alert>
      )}
      {info?.active?.fallback_reason && (
        <Alert severity="warning" sx={{ py: 0.25 }}>
          {info.active.fallback_reason}
        </Alert>
      )}
      {restartNeeded && (
        <Alert severity="warning" sx={{ py: 0.25 }}>
          Restart Guaardvark to apply the new profile. Running now: {activeName}.
        </Alert>
      )}
    </Cluster>
  );
};

export default ProfileSection;
