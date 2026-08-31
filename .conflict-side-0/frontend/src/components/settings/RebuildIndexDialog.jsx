// frontend/src/components/settings/RebuildIndexDialog.jsx
// One door to "throw away index data", with a scope. Replaces the separate
// Purge, Reset and per-profile Clear buttons, which reached three endpoints
// with three different levels of confirmation.
//
//   one profile   POST /api/settings/index_profiles/<name>/rebuild  (drops that projection)
//   everything    POST /api/meta/reset-index                        (index files + every vector table)
//   by document   hands over to PurgeIndexModal (type and date filters)

import React, { useEffect, useState } from "react";
import {
  Box,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  Typography,
} from "@mui/material";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import { ActionButton } from "./ui";
import * as apiService from "../../api/settingsService";

const fmtBytes = (n) => {
  if (!n || n < 1024) return `${n || 0} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(1)} ${units[i]}`;
};

const describeProjection = (p) => {
  const proj = p?.projection || {};
  if (!proj.exists) return "not built";
  return `${(proj.rows ?? 0).toLocaleString()} vectors · ${fmtBytes(proj.size_bytes)}`;
};

/**
 * @param {boolean}  open
 * @param {Array}    profiles   from GET /api/settings/index_profiles
 * @param {function} onClose
 * @param {function} onOpenPurge   caller opens PurgeIndexModal for the by-document scope
 * @param {function} onDone(message, severity)
 */
const RebuildIndexDialog = ({ open, profiles = [], onClose, onOpenPurge, onDone }) => {
  const [scope, setScope] = useState("profile");
  const [profile, setProfile] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open && !profile && profiles.length) {
      setProfile((profiles.find((p) => p.active) || profiles[0]).name);
    }
  }, [open, profiles, profile]);

  const chosen = profiles.find((p) => p.name === profile);

  const run = async () => {
    if (scope === "documents") {
      onClose();
      onOpenPurge?.();
      return;
    }
    setBusy(true);
    try {
      if (scope === "profile") {
        const res = await fetch(`/api/settings/index_profiles/${encodeURIComponent(profile)}/rebuild`, {
          method: "POST",
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok || body?.success === false) throw new Error(body?.error || body?.message || `HTTP ${res.status}`);
        onDone?.(`Cleared the "${profile}" index. Re-index documents to rebuild it.`, "success");
      } else {
        const result = await apiService.resetIndexStorage();
        if (result?.error) throw new Error(result.error.message || result.error);
        onDone?.(result?.message || "Index cleared. Re-index documents to rebuild it.", "success");
      }
      onClose();
    } catch (err) {
      onDone?.(`Rebuild failed: ${err.message}`, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1, color: "error.main", fontSize: "1rem" }}>
        <WarningAmberIcon fontSize="small" />
        Rebuild index
      </DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <Typography variant="body2" color="text.secondary">
          Choose what to throw away. Documents themselves are never removed; the chosen index is emptied and
          rebuilds on the next indexing run.
        </Typography>
        <RadioGroup value={scope} onChange={(e) => setScope(e.target.value)}>
          <FormControlLabel
            value="profile"
            control={<Radio size="small" />}
            label={
              <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                <span>One profile's index</span>
                <FormControl size="small" sx={{ minWidth: 200 }} disabled={scope !== "profile"}>
                  <Select value={profile} onChange={(e) => setProfile(e.target.value)} displayEmpty>
                    {profiles.map((p) => (
                      <MenuItem key={p.name} value={p.name}>
                        {p.name} · {describeProjection(p)}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Box>
            }
          />
          <FormControlLabel
            value="all"
            control={<Radio size="small" />}
            label="Everything: every profile, every vector table and the index files"
          />
          <FormControlLabel
            value="documents"
            control={<Radio size="small" />}
            label="Only some documents, by type or date (opens the filter)"
          />
        </RadioGroup>
        {scope === "profile" && chosen && !chosen.projection?.exists && (
          <Typography variant="caption" color="text.secondary">
            That profile has nothing built yet; there is nothing to clear.
          </Typography>
        )}
        {scope !== "documents" && (
          <Typography variant="caption" sx={{ color: "error.main" }}>
            This cannot be undone. Re-indexing every document takes time and GPU.
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={onClose} disabled={busy}>
          Cancel
        </ActionButton>
        <ActionButton
          kind={scope === "documents" ? "neutral" : "destructive"}
          onClick={run}
          loading={busy}
          disabled={scope === "profile" && (!chosen || !chosen.projection?.exists)}
        >
          {scope === "documents" ? "Choose documents" : scope === "profile" ? "Clear this index" : "Clear everything"}
        </ActionButton>
      </DialogActions>
    </Dialog>
  );
};

export default RebuildIndexDialog;
