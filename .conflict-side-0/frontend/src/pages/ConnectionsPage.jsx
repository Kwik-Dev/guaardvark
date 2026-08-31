import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardActions,
  CardContent,
  Chip,
  CircularProgress,
  Container,
  Divider,
  Grid,
  Menu,
  MenuItem,
  Snackbar,
  Stack,
  Switch,
  Tab,
  Tabs,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import LockIcon from "@mui/icons-material/Lock";
import LockOpenIcon from "@mui/icons-material/LockOpen";

import ConnectionModal from "../components/modals/ConnectionModal";
import {
  deleteConnection,
  fetchConnections,
  fetchEnvironment,
  fetchProviders,
  fetchPublishSettings,
  fetchStoreHealth,
  testConnection,
  updatePublishSettings,
} from "../api/connectionsService";

const FAMILIES = [
  { key: "social", label: "Social" },
  { key: "ai_provider", label: "AI Providers" },
  { key: "mcp_server", label: "MCP" },
];

const STATUS_COLOR = {
  connected: "success",
  error: "error",
  expired: "warning",
  disabled: "default",
  unconfigured: "default",
};

const ConnectionsPage = () => {
  const [family, setFamily] = useState("social");
  const [connections, setConnections] = useState([]);
  const [providers, setProviders] = useState([]);
  const [environment, setEnvironment] = useState([]);
  const [storeHealth, setStoreHealth] = useState(null);
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [feedback, setFeedback] = useState({ open: false, message: "", severity: "info" });
  const [addAnchor, setAddAnchor] = useState(null);
  const [modal, setModal] = useState({ open: false, connection: null, spec: null });
  const [testingId, setTestingId] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [conns, specs, env, health, publishSettings] = await Promise.all([
        fetchConnections(family),
        fetchProviders(family),
        fetchEnvironment(),
        fetchStoreHealth(),
        fetchPublishSettings(),
      ]);
      setConnections(conns);
      setProviders(specs);
      setEnvironment(env);
      setStoreHealth(health);
      setSettings(publishSettings);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [family]);

  useEffect(() => {
    load();
  }, [load]);

  const specByProvider = useMemo(
    () => Object.fromEntries(providers.map((s) => [s.provider, s])),
    [providers],
  );

  const handleTest = useCallback(
    async (connection) => {
      setTestingId(connection.id);
      try {
        const result = await testConnection(connection.id);
        setFeedback({
          open: true,
          message: result.message,
          severity: result.ok ? "success" : "error",
        });
        setConnections((prev) =>
          prev.map((c) => (c.id === connection.id ? result.connection : c)),
        );
      } catch (e) {
        setFeedback({ open: true, message: e.message, severity: "error" });
      } finally {
        setTestingId(null);
      }
    },
    [],
  );

  const handleDelete = useCallback(async (connection) => {
    try {
      await deleteConnection(connection.id);
      setConnections((prev) => prev.filter((c) => c.id !== connection.id));
      setFeedback({ open: true, message: "Connection removed.", severity: "success" });
    } catch (e) {
      setFeedback({ open: true, message: e.message, severity: "error" });
    }
  }, []);

  const handleSettingChange = useCallback(async (key, value) => {
    try {
      const updated = await updatePublishSettings({ [key]: value });
      setSettings(updated);
    } catch (e) {
      setFeedback({ open: true, message: e.message, severity: "error" });
    }
  }, []);

  return (
    <Container maxWidth="lg" sx={{ py: 3 }}>
      <Typography variant="h4" gutterBottom>
        Connections
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Accounts and services this install can publish to or call out to.
        Credentials are stored outside the database.
      </Typography>

      <Tabs value={family} onChange={(_e, v) => setFamily(v)} sx={{ mb: 2 }}>
        {FAMILIES.map((f) => (
          <Tab key={f.key} value={f.key} label={f.label} />
        ))}
      </Tabs>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {family === "social" && settings && (
        <Card variant="outlined" sx={{ mb: 2 }}>
          <CardContent>
            <Stack direction="row" alignItems="center" spacing={2} flexWrap="wrap">
              <Stack direction="row" alignItems="center" spacing={1}>
                <Switch
                  checked={settings.publish_enabled}
                  onChange={(e) => handleSettingChange("publish_enabled", e.target.checked)}
                />
                <Typography variant="body2">Publishing enabled</Typography>
              </Stack>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Switch
                  checked={settings.publish_supervised}
                  onChange={(e) =>
                    handleSettingChange("publish_supervised", e.target.checked)
                  }
                />
                <Tooltip title="Requests from chat or MCP always require approval regardless of this setting.">
                  <Typography variant="body2">Require approval</Typography>
                </Tooltip>
              </Stack>
            </Stack>
          </CardContent>
        </Card>
      )}

      <Box sx={{ mb: 2 }}>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={(e) => setAddAnchor(e.currentTarget)}
          disabled={!providers.length}
        >
          Add connection
        </Button>
        <Menu
          anchorEl={addAnchor}
          open={Boolean(addAnchor)}
          onClose={() => setAddAnchor(null)}
        >
          {providers.map((spec) => (
            <MenuItem
              key={spec.provider}
              onClick={() => {
                setModal({ open: true, connection: null, spec });
                setAddAnchor(null);
              }}
            >
              {spec.label}
              {spec.review_required && (
                <Chip label="app review" size="small" sx={{ ml: 1 }} />
              )}
            </MenuItem>
          ))}
        </Menu>
      </Box>

      {loading ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress />
        </Box>
      ) : (
        <Grid container spacing={2}>
          {connections.map((conn) => {
            const spec = specByProvider[conn.provider];
            return (
              <Grid item xs={12} sm={6} md={4} key={conn.id}>
                <Card variant="outlined" sx={{ height: "100%" }}>
                  <CardContent>
                    <Stack
                      direction="row"
                      justifyContent="space-between"
                      alignItems="flex-start"
                    >
                      <Typography variant="h6">
                        {spec?.label || conn.provider}
                      </Typography>
                      <Chip
                        size="small"
                        label={conn.status}
                        color={STATUS_COLOR[conn.status] || "default"}
                      />
                    </Stack>
                    <Typography variant="body2" color="text.secondary">
                      {conn.handle || conn.display_name || conn.account_slug}
                    </Typography>
                    <Stack direction="row" spacing={0.5} alignItems="center" sx={{ mt: 1 }}>
                      {conn.credential_encrypted ? (
                        <LockIcon fontSize="small" color="success" />
                      ) : (
                        <LockOpenIcon fontSize="small" color="disabled" />
                      )}
                      <Typography variant="caption" color="text.secondary">
                        {conn.has_credentials
                          ? `${conn.credential_hint} · ${conn.credential_source}`
                          : "No credentials"}
                      </Typography>
                    </Stack>
                    {conn.error_message && (
                      <Alert severity="error" sx={{ mt: 1 }}>
                        {conn.error_message}
                      </Alert>
                    )}
                  </CardContent>
                  <CardActions>
                    <Button
                      size="small"
                      onClick={() => handleTest(conn)}
                      disabled={testingId === conn.id}
                    >
                      {testingId === conn.id ? "Testing…" : "Test"}
                    </Button>
                    <Button
                      size="small"
                      onClick={() =>
                        setModal({ open: true, connection: conn, spec })
                      }
                    >
                      Edit
                    </Button>
                    <Button size="small" color="error" onClick={() => handleDelete(conn)}>
                      Remove
                    </Button>
                  </CardActions>
                </Card>
              </Grid>
            );
          })}
          {!connections.length && (
            <Grid item xs={12}>
              <Alert severity="info">
                No connections in this category yet.
              </Alert>
            </Grid>
          )}
        </Grid>
      )}

      {environment.length > 0 && (
        <Box sx={{ mt: 4 }}>
          <Divider sx={{ mb: 2 }} />
          <Typography variant="h6" gutterBottom>
            Environment
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Detected from the backend process environment. Managed outside the
            app and shown here read-only.
          </Typography>
          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
            {environment.map((entry) => (
              <Chip
                key={entry.env_key}
                label={`${entry.env_key} · ${entry.hint}`}
                variant="outlined"
              />
            ))}
          </Stack>
        </Box>
      )}

      {storeHealth && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 3 }}>
          {storeHealth.encrypted ? "Encrypted" : "Unencrypted"} ·{" "}
          {storeHealth.record_count} credential(s) · {storeHealth.path}
          {storeHealth.mode ? ` (${storeHealth.mode})` : ""}
        </Typography>
      )}

      <ConnectionModal
        open={modal.open}
        connection={modal.connection}
        providerSpec={modal.spec}
        onClose={() => setModal({ open: false, connection: null, spec: null })}
        onFeedback={setFeedback}
        onSaved={load}
      />

      <Snackbar
        open={feedback.open}
        autoHideDuration={5000}
        onClose={() => setFeedback((f) => ({ ...f, open: false }))}
      >
        <Alert
          severity={feedback.severity}
          onClose={() => setFeedback((f) => ({ ...f, open: false }))}
        >
          {feedback.message}
        </Alert>
      </Snackbar>
    </Container>
  );
};

export default ConnectionsPage;
