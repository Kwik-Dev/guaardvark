import React, { useCallback, useEffect, useState } from "react";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Link,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import CollapsibleAlert from "../common/CollapsibleAlert";

import {
  completeOAuth,
  createConnection,
  startOAuth,
  updateConnection,
} from "../../api/connectionsService";

const MASK_PLACEHOLDER = "••••••••••••••••";

/**
 * Create or edit a connection. Fields are rendered from the provider spec, so
 * a provider added on the backend appears here without a frontend change.
 */
const ConnectionModal = ({ open, onClose, connection, providerSpec, onFeedback, onSaved }) => {
  const spec = providerSpec;
  const isEdit = Boolean(connection?.id);

  const [accountSlug, setAccountSlug] = useState("default");
  const [displayName, setDisplayName] = useState("");
  const [config, setConfig] = useState({});
  const [secrets, setSecrets] = useState({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [oauth, setOauth] = useState(null);
  const [oauthCode, setOauthCode] = useState("");

  const isOAuth = spec?.auth_kinds?.includes("oauth2");
  const redirectUri =
    typeof window !== "undefined" ? `${window.location.origin}/connections` : "";

  useEffect(() => {
    if (!open) return;
    setAccountSlug(connection?.account_slug || "default");
    setDisplayName(connection?.display_name || "");
    setConfig(connection?.config || {});
    setSecrets({});
    setError(null);
    setOauth(null);
    setOauthCode("");
  }, [open, connection]);

  const configuredFields = connection?.credential_fields || [];
  const isEnvManaged = connection?.credential_source === "env";

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        family: spec.family,
        provider: spec.provider,
        account_slug: accountSlug.trim() || "default",
        display_name: displayName,
        auth_kind: spec.auth_kinds[0],
        config,
        ...secrets,
      };
      const saved = isEdit
        ? await updateConnection(connection.id, payload)
        : await createConnection(payload);
      onFeedback?.({
        open: true,
        message: isEdit ? "Connection updated." : "Connection created.",
        severity: "success",
      });
      onSaved?.(saved);
      if (!isOAuth) onClose?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }, [
    spec, accountSlug, displayName, config, secrets, isEdit, connection,
    isOAuth, onClose, onFeedback, onSaved,
  ]);

  const handleStartOAuth = useCallback(async () => {
    setError(null);
    try {
      const result = await startOAuth(connection.id, redirectUri);
      setOauth(result);
    } catch (e) {
      setError(e.message);
    }
  }, [connection, redirectUri]);

  const handleCompleteOAuth = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      const result = await completeOAuth(connection.id, oauthCode.trim(), redirectUri);
      onFeedback?.({
        open: true,
        message: result.message || "Authorized.",
        severity: result.ok ? "success" : "warning",
      });
      onSaved?.(result.connection);
      onClose?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }, [connection, oauthCode, redirectUri, onClose, onFeedback, onSaved]);

  if (!spec) return null;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {isEdit ? `Edit ${spec.label}` : `Connect ${spec.label}`}
      </DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {spec.setup_help && (
            <Typography variant="body2" color="text.secondary">
              {spec.setup_help}
              {spec.docs_url && (
                <>
                  {" "}
                  <Link href={spec.docs_url} target="_blank" rel="noopener">
                    Documentation
                  </Link>
                </>
              )}
            </Typography>
          )}

          {spec.review_required && (
            <CollapsibleAlert severity="warning">
              This platform requires app review before its API can post on your
              behalf.
            </CollapsibleAlert>
          )}

          {isEnvManaged && (
            <CollapsibleAlert severity="info">
              This credential comes from the environment variable{" "}
              <code>{connection.credential_env_key}</code> and is managed outside
              the app.
            </CollapsibleAlert>
          )}

          {error && <CollapsibleAlert severity="error">{error}</CollapsibleAlert>}

          <TextField
            label="Account name"
            value={accountSlug}
            onChange={(e) => setAccountSlug(e.target.value)}
            disabled={isEdit}
            size="small"
            helperText="Distinguishes multiple accounts on the same platform."
          />
          <TextField
            label="Display name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            size="small"
          />

          {spec.config_fields.map((field) => (
            <TextField
              key={field.name}
              label={field.label}
              required={field.required}
              size="small"
              select={field.choices?.length > 0}
              value={config[field.name] ?? field.default ?? ""}
              onChange={(e) =>
                setConfig((prev) => ({ ...prev, [field.name]: e.target.value }))
              }
              helperText={field.help}
            >
              {field.choices?.map((choice) => (
                <MenuItem key={choice} value={choice}>
                  {choice}
                </MenuItem>
              ))}
            </TextField>
          ))}

          {!isEnvManaged &&
            spec.credential_fields.map((field) => {
              const alreadySet = configuredFields.includes(field.name);
              return (
                <TextField
                  key={field.name}
                  label={field.label}
                  type="password"
                  size="small"
                  autoComplete="new-password"
                  placeholder={alreadySet ? MASK_PLACEHOLDER : field.placeholder}
                  value={secrets[field.name] ?? ""}
                  onChange={(e) =>
                    setSecrets((prev) => ({ ...prev, [field.name]: e.target.value }))
                  }
                  helperText={
                    alreadySet
                      ? `Configured (${connection.credential_hint}). Leave blank to keep.`
                      : field.help
                  }
                />
              );
            })}

          {isOAuth && isEdit && (
            <Box>
              <Button variant="outlined" onClick={handleStartOAuth} size="small">
                Authorize with {spec.label}
              </Button>
              {oauth && (
                <Stack spacing={1} sx={{ mt: 2 }}>
                  <Typography variant="body2">
                    Open this URL, approve access, then paste the code below.
                  </Typography>
                  <TextField
                    value={oauth.authorize_url}
                    size="small"
                    InputProps={{ readOnly: true }}
                    onFocus={(e) => e.target.select()}
                  />
                  <TextField
                    label="Authorization code"
                    value={oauthCode}
                    onChange={(e) => setOauthCode(e.target.value)}
                    size="small"
                  />
                  <Button
                    variant="contained"
                    size="small"
                    disabled={!oauthCode.trim() || saving}
                    onClick={handleCompleteOAuth}
                  >
                    Complete authorization
                  </Button>
                </Stack>
              )}
            </Box>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ConnectionModal;
