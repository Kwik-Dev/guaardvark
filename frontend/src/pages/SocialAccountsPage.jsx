// frontend/src/pages/SocialAccountsPage.jsx
//
// The person's social accounts, one panel per platform, built on Connections
// the way WordPress Sites is built on its sites. Add and Edit reuse
// ConnectionModal. Each platform's rules (rate limits, AI disclosure, how an
// account declares automation, scheduling, delete) come from its provider
// spec; nothing here knows a platform by name. Social Posts lists what went out.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Collapse,
  IconButton,
  Link,
  Menu,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import RefreshIcon from "@mui/icons-material/Refresh";
import { useNavigate, useSearchParams } from "react-router-dom";

import PageLayout from "../components/layout/PageLayout";
import ConnectionModal from "../components/modals/ConnectionModal";
import { useSnackbar } from "../components/common/SnackbarProvider";
import {
  ActionButton,
  Cluster,
  ConfirmActionDialog,
  Hint,
  Line,
  SettingChip,
  SettingsPanel,
  StatusPill,
} from "../components/settings/ui";
import {
  deleteConnection,
  fetchConnectionUsage,
  fetchConnections,
  fetchEnvironment,
  fetchProviders,
  fetchPublishSettings,
  fetchStoreHealth,
  testConnection,
  updatePublishSettings,
} from "../api/connectionsService";
import {
  describeDelete,
  describeDisclosure,
  describeRateLimit,
  describeScheduling,
} from "../components/social/platformRules";
import copyText from "../utils/copyText";

const STATUS_TONE = {
  connected: "ok",
  error: "error",
  expired: "warn",
  disabled: "neutral",
  unconfigured: "neutral",
};

const formatWhen = (iso) => (iso ? new Date(iso).toLocaleString() : "never");

const usageTone = (used, posts) => (used >= posts ? "error" : used >= posts * 0.8 ? "warn" : "ok");

/** How the account declares automation: its state where the provider reads it. */
const BotLabelLine = ({ label, connection }) => {
  if (!label || label.kind === "none") return null;
  if (label.kind === "automatic") return <Hint>{label.how}</Hint>;
  const state = connection.config?.bot_label_set;
  return (
    <Line>
      {label.readable && (
        <StatusPill
          label={
            state === true ? "Bot label set" : state === false ? "Bot label not set" : "Bot label not read yet"
          }
          tone={state === true ? "ok" : state === false ? "warn" : "neutral"}
          tooltip={state === undefined ? "Run Test to read it from the platform." : ""}
        />
      )}
      <Hint>{label.how}</Hint>
    </Line>
  );
};

const RulesBlock = ({ capabilities }) => {
  const rule = capabilities.ai_disclosure;
  const label = capabilities.bot_label;
  const source = (url) =>
    url ? (
      <>
        {" "}
        <Link href={url} target="_blank" rel="noopener noreferrer">
          Source
        </Link>
      </>
    ) : null;
  return (
    <Stack spacing={0.75} sx={{ pt: 0.5 }}>
      {(capabilities.rate_limits || []).map((limit) => (
        <Hint key={`${limit.posts}-${limit.window_seconds}`}>
          Rate limit: {describeRateLimit(limit)}
          {source(limit.source)}
        </Hint>
      ))}
      {rule && (
        <Hint>
          AI disclosure: {describeDisclosure(rule)}
          {source(rule.source)}
        </Hint>
      )}
      {label && label.kind !== "none" && (
        <Hint>
          Automation label: {label.how}
          {source(label.source)}
        </Hint>
      )}
      {label && label.kind === "none" && (
        <Hint>
          Automation: {label.how}
          {source(label.source)}
        </Hint>
      )}
      <Hint>
        Scheduling: {describeScheduling(capabilities)}
        {source(capabilities.native_scheduling?.source)}
      </Hint>
      <Hint>Delete: {describeDelete(capabilities)}</Hint>
    </Stack>
  );
};

const SocialAccountsPage = () => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { showMessage } = useSnackbar();

  const [connections, setConnections] = useState([]);
  const [providers, setProviders] = useState([]);
  const [usage, setUsage] = useState({});
  const [settings, setSettings] = useState(null);
  const [environment, setEnvironment] = useState([]);
  const [storeHealth, setStoreHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [addAnchor, setAddAnchor] = useState(null);
  const [modal, setModal] = useState({ open: false, connection: null, spec: null });
  const [testingId, setTestingId] = useState(null);
  const [removing, setRemoving] = useState(null);
  const [busy, setBusy] = useState(false);
  const [rulesOpen, setRulesOpen] = useState({});

  // The OAuth redirect (ConnectionModal) lands on /connections, which forwards
  // here with the query intact; the code is pasted in the tab that started it.
  const oauthCode = searchParams.get("code");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [conns, specs, use, publishSettings, env, health] = await Promise.all([
        fetchConnections("social"),
        fetchProviders("social"),
        fetchConnectionUsage("social"),
        fetchPublishSettings(),
        fetchEnvironment(),
        fetchStoreHealth(),
      ]);
      setConnections(conns);
      setProviders(specs);
      setUsage(use);
      setSettings(publishSettings);
      setEnvironment(env);
      setStoreHealth(health);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const byProvider = useMemo(() => {
    const groups = {};
    for (const conn of connections) {
      (groups[conn.provider] = groups[conn.provider] || []).push(conn);
    }
    return groups;
  }, [connections]);

  const handleTest = async (connection) => {
    setTestingId(connection.id);
    try {
      const result = await testConnection(connection.id);
      showMessage(result.message, result.ok ? "success" : "error");
      setConnections((prev) => prev.map((c) => (c.id === connection.id ? result.connection : c)));
    } catch (e) {
      showMessage(e.message, "error");
    } finally {
      setTestingId(null);
    }
  };

  const handleRemove = async () => {
    if (!removing) return;
    setBusy(true);
    try {
      await deleteConnection(removing.id);
      showMessage("Account removed.", "success");
      setRemoving(null);
      await load();
    } catch (e) {
      showMessage(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const handleSetting = async (key, value) => {
    try {
      setSettings(await updatePublishSettings({ [key]: value }));
    } catch (e) {
      showMessage(e.message, "error");
    }
  };

  const openPosts = (connection) =>
    navigate(connection ? `/social/posts?connection_id=${connection.id}` : "/social/posts");

  const dismissCode = () => {
    const next = new URLSearchParams(searchParams);
    ["code", "scope", "state", "authuser", "prompt"].forEach((key) => next.delete(key));
    setSearchParams(next, { replace: true });
  };

  const renderAccount = (conn, spec) => {
    const stats = usage[String(conn.id)] || {};
    return (
      <Box
        key={conn.id}
        sx={{ py: 1.25, borderTop: 1, borderColor: "divider", "&:first-of-type": { borderTop: 0, pt: 0 } }}
      >
        <Stack spacing={0.75}>
          <Line>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {conn.display_name || conn.handle || conn.account_slug}
            </Typography>
            {conn.handle && conn.handle !== conn.display_name && (
              <Typography variant="caption" color="text.secondary">
                {conn.handle}
              </Typography>
            )}
            <StatusPill label={conn.status} tone={STATUS_TONE[conn.status] || "neutral"} />
            <Typography variant="caption" color="text.secondary">
              {conn.has_credentials
                ? `${conn.auth_kind.replace("_", " ")} · ${conn.credential_hint}`
                : "No credentials"}
            </Typography>
          </Line>
          <Line>
            <Hint>
              {stats.posts || 0} posts · last {formatWhen(stats.last_posted_at)}
              {stats.scheduled ? ` · ${stats.scheduled} scheduled` : ""}
            </Hint>
            {(stats.rate_limits || []).map((limit) => (
              <StatusPill
                key={limit.window_seconds}
                label={`${limit.used}/${limit.posts} per ${limit.window}`}
                tone={usageTone(limit.used, limit.posts)}
                tooltip={describeRateLimit(limit)}
              />
            ))}
          </Line>
          <BotLabelLine label={spec?.capabilities?.bot_label} connection={conn} />
          {conn.error_message && (
            <Alert severity="error" sx={{ py: 0 }}>
              {conn.error_message}
            </Alert>
          )}
          <Line>
            <ActionButton loading={testingId === conn.id} onClick={() => handleTest(conn)}>
              Test
            </ActionButton>
            <ActionButton onClick={() => setModal({ open: true, connection: conn, spec })} disabled={!spec}>
              Edit
            </ActionButton>
            <ActionButton onClick={() => openPosts(conn)}>Posts</ActionButton>
            <ActionButton kind="destructive" onClick={() => setRemoving(conn)}>
              Remove
            </ActionButton>
          </Line>
        </Stack>
      </Box>
    );
  };

  return (
    <PageLayout
      title="Social Accounts"
      actions={
        <Stack direction="row" spacing={1} alignItems="center">
          <ActionButton kind="link" onClick={() => openPosts(null)}>
            Social Posts
          </ActionButton>
          <ActionButton kind="link" onClick={() => navigate("/approvals")}>
            Approvals
          </ActionButton>
          <ActionButton startIcon={<AddIcon />} onClick={(e) => setAddAnchor(e.currentTarget)} disabled={!providers.length}>
            Add account
          </ActionButton>
          <Tooltip title="Refresh">
            <IconButton onClick={load} size="small">
              <RefreshIcon />
            </IconButton>
          </Tooltip>
        </Stack>
      }
    >
      <Menu anchorEl={addAnchor} open={Boolean(addAnchor)} onClose={() => setAddAnchor(null)}>
        {providers.map((spec) => (
          <MenuItem
            key={spec.provider}
            onClick={() => {
              setModal({ open: true, connection: null, spec });
              setAddAnchor(null);
            }}
          >
            {spec.label}
          </MenuItem>
        ))}
      </Menu>

      {oauthCode && (
        <Alert severity="info" sx={{ mb: 2 }} onClose={dismissCode}>
          <Stack spacing={1}>
            <Typography variant="body2">
              The platform sent an authorization code. Copy it into the Authorize step of the
              account dialog in the tab where you started.
            </Typography>
            <Stack direction="row" spacing={1} alignItems="center">
              <TextField
                value={oauthCode}
                size="small"
                InputProps={{ readOnly: true }}
                onFocus={(e) => e.target.select()}
                sx={{ flex: 1, maxWidth: 520 }}
              />
              <ActionButton onClick={() => copyText(oauthCode)}>Copy</ActionButton>
            </Stack>
          </Stack>
        </Alert>
      )}

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {settings?.posting_stopped && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {settings.posting_stop_reason || "Public posting is stopped. Resume it on the Outreach page."}{" "}
          Queued publishes are held on the Approvals page; a scheduled post whose time comes
          meanwhile is held there too.
        </Alert>
      )}

      <Stack spacing={2}>
        {settings && (
          <SettingsPanel
            title="Publishing"
            description="Applies to every account on this page"
            help="Publishing is posting your own content through these accounts. Outreach (replies on other people's threads) has its own switches on the Outreach page."
          >
            <Cluster label="Switches">
              <Line>
                <SettingChip
                  label="Publishing"
                  on={settings.publish_enabled}
                  onToggle={(value) => handleSetting("publish_enabled", value)}
                />
                <SettingChip
                  label="Require approval"
                  on={settings.publish_supervised}
                  onToggle={(value) => handleSetting("publish_supervised", value)}
                  tooltip="Requests from chat or MCP always wait for approval, whatever this says."
                />
              </Line>
              <Hint>
                A scheduled post waits for approval like any other; once approved it goes out at its
                time, sent by the platform where it schedules natively, otherwise by Guaardvark.
              </Hint>
            </Cluster>
          </SettingsPanel>
        )}

        {loading && !providers.length ? (
          <Hint>Loading accounts…</Hint>
        ) : (
          providers.map((spec) => {
            const accounts = byProvider[spec.provider] || [];
            const open = Boolean(rulesOpen[spec.provider]);
            return (
              <SettingsPanel
                key={spec.provider}
                title={spec.label}
                description={
                  accounts.length
                    ? `${accounts.length} account${accounts.length === 1 ? "" : "s"}`
                    : "Not connected"
                }
                actions={
                  <>
                    <ActionButton
                      kind="link"
                      onClick={() => setRulesOpen((prev) => ({ ...prev, [spec.provider]: !open }))}
                    >
                      {open ? "Hide platform rules" : "Platform rules"}
                    </ActionButton>
                    <ActionButton onClick={() => setModal({ open: true, connection: null, spec })}>
                      {accounts.length ? "Add another" : "Connect"}
                    </ActionButton>
                  </>
                }
              >
                {accounts.map((conn) => renderAccount(conn, spec))}
                {!accounts.length && <Hint>{spec.setup_help}</Hint>}
                <Collapse in={open} unmountOnExit>
                  <RulesBlock capabilities={spec.capabilities || {}} />
                </Collapse>
              </SettingsPanel>
            );
          })
        )}

        {connections
          .filter((conn) => !providers.some((spec) => spec.provider === conn.provider))
          .map((conn) => (
            <Alert key={conn.id} severity="warning">
              {conn.display_name || conn.provider}: its provider ({conn.provider}) is not available
              on this install.
            </Alert>
          ))}
      </Stack>

      {environment.length > 0 && (
        <Box sx={{ mt: 3 }}>
          <Typography variant="subtitle2">From the environment</Typography>
          <Hint>Detected in the backend process environment, managed outside the app and shown read-only.</Hint>
          <Line sx={{ mt: 1 }}>
            {environment.map((entry) => (
              <StatusPill key={entry.env_key} label={`${entry.env_key} · ${entry.hint}`} />
            ))}
          </Line>
        </Box>
      )}

      {storeHealth && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 3 }}>
          Credentials are stored outside the database: {storeHealth.encrypted ? "encrypted" : "unencrypted"} ·{" "}
          {storeHealth.record_count} credential(s) · {storeHealth.path}
          {storeHealth.mode ? ` (${storeHealth.mode})` : ""}
        </Typography>
      )}

      <ConnectionModal
        open={modal.open}
        connection={modal.connection}
        providerSpec={modal.spec}
        onClose={() => setModal({ open: false, connection: null, spec: null })}
        onFeedback={(feedback) => showMessage(feedback.message, feedback.severity)}
        onSaved={load}
      />

      <ConfirmActionDialog
        open={Boolean(removing)}
        title="Remove this account?"
        description="Guaardvark forgets the account and its stored credentials. Nothing is deleted on the platform."
        facts={
          removing
            ? [
                { label: "Account", value: removing.display_name || removing.handle || removing.account_slug },
                { label: "Platform", value: removing.provider },
              ]
            : []
        }
        keeps="the account itself and its posts on the platform; the post history here"
        confirmLabel="Remove"
        busy={busy}
        onConfirm={handleRemove}
        onClose={() => setRemoving(null)}
      />
    </PageLayout>
  );
};

export default SocialAccountsPage;
