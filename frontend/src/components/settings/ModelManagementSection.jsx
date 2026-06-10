// frontend/src/components/settings/ModelManagementSection.jsx
// Extracted from SettingsPage.jsx - Model Management functionality

import React, { useState, useEffect, useCallback } from 'react';
import {
  Typography,
  Box,
  Select,
  MenuItem,
  Button,
  FormControl,
  InputLabel,
  CircularProgress,
  Paper,
  Grid,
  Tooltip,
  ToggleButton,
  ToggleButtonGroup,
  Divider,
  Alert
} from '@mui/material';
import { useSnackbar } from '../../contexts/SnackbarProvider';
import apiService from '../../api/apiService';
import {
  getLlmProvider,
  setLlmProvider,
  getMistralModels,
  setMistralModel,
  testMistral,
} from '../../api/modelService';

const ModelManagementSection = ({ 
  availableModels, 
  selectedModel, 
  setSelectedModel,
  activeModel,
  isLoading,
  refreshActiveModel 
}) => {
  const { showMessage } = useSnackbar();

  // --- LLM provider (Ollama vs Mistral cloud) ---
  const [provider, setProvider] = useState('ollama');
  const [mistralAvailable, setMistralAvailable] = useState(false);
  const [mistralModel, setMistralModelState] = useState('');
  const [mistralModels, setMistralModels] = useState([]);
  const [providerBusy, setProviderBusy] = useState(false);
  const [testingMistral, setTestingMistral] = useState(false);

  const loadProvider = useCallback(async () => {
    try {
      const info = await getLlmProvider();
      setProvider(info?.provider || 'ollama');
      setMistralAvailable(!!info?.mistral_available);
      setMistralModelState(info?.mistral_model || '');
      if (info?.mistral_available) {
        try {
          setMistralModels(await getMistralModels());
        } catch {
          /* model list is best-effort; toggle still works */
        }
      }
    } catch (err) {
      // Endpoint missing / backend down — leave defaults, don't spam the user.
      console.error('Failed to load LLM provider info:', err.message);
    }
  }, []);

  useEffect(() => {
    loadProvider();
  }, [loadProvider]);

  const handleProviderChange = async (_e, next) => {
    if (!next || next === provider) return;
    setProviderBusy(true);
    try {
      await setLlmProvider(next);
      setProvider(next);
      showMessage(
        next === 'mistral'
          ? 'Chat now routes to Mistral (cloud API).'
          : 'Chat now uses local Ollama.',
        'success',
      );
      if (next === 'mistral' && mistralModels.length === 0) {
        try {
          setMistralModels(await getMistralModels());
        } catch {
          /* ignore */
        }
      }
    } catch (err) {
      showMessage(`Could not switch provider: ${err.message}`, 'error');
    } finally {
      setProviderBusy(false);
    }
  };

  const handleMistralModelChange = async (e) => {
    const model = e.target.value;
    setMistralModelState(model);
    try {
      await setMistralModel(model);
      showMessage(`Mistral model set to ${model}.`, 'success');
    } catch (err) {
      showMessage(`Could not set Mistral model: ${err.message}`, 'error');
    }
  };

  const handleTestMistral = async () => {
    setTestingMistral(true);
    try {
      const res = await testMistral();
      showMessage(`Mistral OK: "${res?.response || 'connected'}"`, 'success');
    } catch (err) {
      showMessage(`Mistral test failed: ${err.message}`, 'error');
    } finally {
      setTestingMistral(false);
    }
  };

  const handleActionClick = async (
    actionFunction,
    actionArgs,
    confirmMessage,
    loadingMessage,
    successMessage,
    failureMessagePrefix,
  ) => {
    if (confirmMessage && !window.confirm(confirmMessage)) return;

    showMessage(loadingMessage || "Processing...", "info");
    try {
      const result = await actionFunction(...actionArgs);
      if (result?.error && !result.warning && result.error !== "User aborted") {
        throw new Error(result.error.message || result.error);
      }
      const message =
        result?.warning ||
        result?.message ||
        successMessage ||
        "Action completed successfully.";
      const severity = result?.warning ? "warning" : "success";

      showMessage(message, severity);

      if (actionFunction === apiService.setModel) {
        refreshActiveModel();
      }
    } catch (err) {
      if (err.message !== "User aborted") {
        showMessage(`${failureMessagePrefix}: ${err.message}`, "error");
      }
    }
  };

  const handleSetModelClick = () => {
    if (!selectedModel) {
      showMessage("Please select a model first.", "warning");
      return;
    }
    handleActionClick(
      apiService.setModel,
      [selectedModel],
      null,
      "Setting active model...",
      `Model set to ${selectedModel}.`,
      "Failed to set model",
    );
  };

  const handleRefreshModelsClick = () => {
    handleActionClick(
      apiService.refreshModels,
      [],
      null,
      "Refreshing available models...",
      "Models refreshed successfully.",
      "Failed to refresh models",
    );
  };

  return (
    <Paper elevation={3} sx={{ p: 2 }}>
      <Typography variant="h6" gutterBottom>
        Model Management
      </Typography>

      {/* --- LLM provider choice: local Ollama vs Mistral cloud API --- */}
      <Box sx={{ mb: 2 }}>
        <Typography variant="body2" color="text.secondary" gutterBottom>
          LLM Provider
        </Typography>
        <ToggleButtonGroup
          exclusive
          color="primary"
          value={provider}
          onChange={handleProviderChange}
          disabled={providerBusy}
          size="small"
        >
          <ToggleButton value="ollama">Ollama (local)</ToggleButton>
          <Tooltip
            title={
              mistralAvailable
                ? 'Route chat to Mistral’s hosted API'
                : 'Set MISTRAL_API_KEY in .env to enable'
            }
          >
            <span>
              <ToggleButton value="mistral" disabled={!mistralAvailable}>
                Mistral (cloud)
              </ToggleButton>
            </span>
          </Tooltip>
        </ToggleButtonGroup>

        {provider === 'mistral' && (
          <Box sx={{ mt: 2 }}>
            <Alert severity="info" sx={{ mb: 2 }}>
              Chat generation is using Mistral’s cloud API. Embeddings/RAG stay on
              local Ollama. Requests leave your machine.
            </Alert>
            <Grid container spacing={2} alignItems="center">
              <Grid item xs={12} md={8}>
                <FormControl fullWidth size="small">
                  <InputLabel>Mistral Model</InputLabel>
                  <Select
                    value={mistralModel}
                    label="Mistral Model"
                    onChange={handleMistralModelChange}
                  >
                    {(mistralModels.length
                      ? mistralModels
                      : [{ name: mistralModel, id: mistralModel }]
                    ).map((m) => (
                      <MenuItem key={m.id || m.name} value={m.name}>
                        {m.name}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} md={4}>
                <Button
                  variant="outlined"
                  fullWidth
                  onClick={handleTestMistral}
                  disabled={testingMistral}
                >
                  {testingMistral ? <CircularProgress size={22} /> : 'Test Connection'}
                </Button>
              </Grid>
            </Grid>
          </Box>
        )}
      </Box>
      <Divider sx={{ mb: 2 }} />
      <Typography variant="body2" color="text.secondary" gutterBottom>
        {provider === 'mistral'
          ? 'Ollama models (active when provider is set back to Ollama):'
          : 'Ollama models:'}
      </Typography>

      <Grid container spacing={2}>
        <Grid item xs={12}>
          <Typography variant="body2" color="text.secondary" gutterBottom>
            Current Active Model: <strong>{activeModel || "Loading..."}</strong>
          </Typography>
        </Grid>
        <Grid item xs={12} md={6}>
          <FormControl fullWidth disabled={isLoading}>
            <InputLabel>Select Model</InputLabel>
            <Select
              value={selectedModel}
              label="Select Model"
              onChange={(e) => setSelectedModel(e.target.value)}
            >
              {availableModels.map((model) => (
                <MenuItem key={model} value={model}>
                  {model}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Grid>
        <Grid item xs={12} md={6}>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Tooltip title="Set the selected model as active">
              <span>
                <Button
                  variant="contained"
                  onClick={handleSetModelClick}
                  disabled={isLoading || !selectedModel}
                  fullWidth
                >
                  {isLoading ? (
                    <CircularProgress size={24} color="inherit" />
                  ) : (
                    "Set Model"
                  )}
                </Button>
              </span>
            </Tooltip>
          </Box>
        </Grid>
        <Grid item xs={12}>
          <Tooltip title="Refresh the list of available models">
            <span>
              <Button
                variant="outlined"
                onClick={handleRefreshModelsClick}
                disabled={isLoading}
                fullWidth
              >
                {isLoading ? (
                  <CircularProgress size={24} />
                ) : (
                  "Refresh Models"
                )}
              </Button>
            </span>
          </Tooltip>
        </Grid>
      </Grid>
    </Paper>
  );
};

export default ModelManagementSection; 