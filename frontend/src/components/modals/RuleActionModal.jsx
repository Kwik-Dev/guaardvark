// frontend/src/components/modals/RuleActionModal.jsx
// No Level or Type dropdowns: a new rule's level and type are derived from the
// form (COMMAND when it has a command label, SYSTEM for the two system prompt
// names, PROMPT otherwise); an existing rule keeps the pair it was stored with.

import CloseIcon from "@mui/icons-material/Close";
import LinkIcon from "@mui/icons-material/Link";
import {
  Autocomplete,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Grid,
  IconButton,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import CollapsibleAlert from "../common/CollapsibleAlert";
import React, { useCallback, useEffect, useState } from "react";
import { useTheme } from "@mui/material/styles";

import * as apiService from "../../api";

const ALL_MODELS_VALUE = "__ALL__";

// Database type and level for a NEW rule, derived from the form. An existing
// rule keeps the level and type it was stored with (see effectiveTypeAndLevel):
// the chat engine looks rows up by them (qa_default must stay QA_TEMPLATE at
// SYSTEM, SYSTEM and LEARNED rows must keep their level), so re-deriving them
// from the name on every save silently broke retrieval.
const deriveTypeAndLevel = (formData) => {
  const name = formData.name?.trim().toLowerCase() || "";
  if (formData.command_label?.trim()) {
    return { type: "COMMAND_RULE", level: "USER_GLOBAL" };
  }
  if (name === "qa_default") {
    return { type: "QA_TEMPLATE", level: "SYSTEM" };
  }
  if (LOOKED_UP_NAMES.includes(name)) {
    return { type: "PROMPT_TEMPLATE", level: "SYSTEM" };
  }
  return { type: "PROMPT_TEMPLATE", level: "USER_GLOBAL" };
};

// Rules the engine finds by name (rule_utils.PROMPT_SLOT_RULE_NAMES and the
// QA template): renaming one would leave the lookup empty, so the name of an
// existing one is read-only here.
export const LOOKED_UP_NAMES = [
  "global_default_chat_system_prompt",
  "enhanced_chat",
  "qa_default",
];

// An existing rule keeps its stored level and type; type is nullable, so a
// null stays null (and is left out of the save) rather than being derived.
export const effectiveTypeAndLevel = (formData, ruleData) => {
  if (ruleData?.id && ruleData.level) {
    return { type: ruleData.type || null, level: ruleData.level };
  }
  return deriveTypeAndLevel(formData);
};

// Label and hint of the big text field, by the type this save will store.
export const contentFieldFor = (type) => {
  switch (type) {
    case "PROMPT_TEMPLATE":
    case "SYSTEM_PROMPT":
      return {
        label: "Prompt Template",
        helperText: "Enter the prompt template with {placeholders} as needed",
      };
    case "QA_TEMPLATE":
      return {
        label: "QA Template",
        helperText:
          "The answer-from-context template; keep {context_str} and {query_str}",
      };
    case "COMMAND_RULE":
      return {
        label: "Command Prompt",
        helperText:
          "The prompt the command runs; {{args}} is replaced by what follows the command",
      };
    default:
      return {
        label: "Rule Content",
        helperText: "Define the rule logic or content",
      };
  }
};

// The badge shown in the dialog: COMMAND, SYSTEM, LEARNED or PROMPT.
const displayKind = ({ type, level }) => {
  if (type === "COMMAND_RULE") return "COMMAND";
  if (level === "SYSTEM") return "SYSTEM";
  if (level === "LEARNED") return "LEARNED";
  return "PROMPT";
};

const RuleActionModal = ({
  open,
  onClose,
  ruleData,
  onSave,
  onDelete,
  onOpenLinker,
  isSaving,
}) => {
  const isNewRule = !ruleData?.id;
  const theme = useTheme();
  const [formData, setFormData] = useState({});
  const [modelOptions, setModelOptions] = useState([ALL_MODELS_VALUE]);
  const [isLoadingModels, setIsLoadingModels] = useState(false);
  const [modelFetchError, setModelFetchError] = useState(null);

  const initializeFormData = useCallback(() => {
    const initialData = {
      name: ruleData?.name || "",
      description: ruleData?.description || "",
      rule_text: ruleData?.rule_text || "",
      command_label: ruleData?.command_label || "",
      target_models:
        ruleData?.target_models?.length > 0
          ? ruleData.target_models
          : [ALL_MODELS_VALUE],
      is_active: ruleData?.is_active !== false,
      project_id: ruleData?.project_id || null,
      client_id: ruleData?.client_id || null,
      level: ruleData?.level || null,
      type: ruleData?.type || null,
    };
    setFormData(initialData);
  }, [ruleData]);

  useEffect(() => {
    if (open) {
      initializeFormData();

      const fetchModels = async () => {
        setIsLoadingModels(true);
        setModelFetchError(null);
        try {
          const fetchedApiModels = await apiService.getAvailableModels();
          if (fetchedApiModels.error) {
            throw new Error(
              fetchedApiModels.error.message || fetchedApiModels.error
            );
          }
          const names = Array.isArray(fetchedApiModels)
            ? fetchedApiModels.map((m) => m.name).filter(Boolean)
            : [];
          setModelOptions([ALL_MODELS_VALUE, ...new Set(names)]);
          if (names.length === 0) {
            setModelFetchError(
              "No installed models were listed (is Ollama running?). " +
                "Only All Models can be chosen; models already set on this rule are kept."
            );
          }
        } catch (err) {
          console.error("RuleActionModal: Failed to fetch models:", err);
          // Local-first: no made-up model names when the local list is
          // unavailable. The rule's existing targets stay selectable as-is.
          setModelFetchError(
            `Could not load the installed model list: ${err.message}. ` +
              "Only All Models can be chosen; models already set on this rule are kept."
          );
          setModelOptions([ALL_MODELS_VALUE]);
        } finally {
          setIsLoadingModels(false);
        }
      };
      fetchModels();
    }
  }, [open, initializeFormData]);

  const handleChange = (event) => {
    const { name, value, type, checked } = event.target;
    const newFormData = {
      ...formData,
      [name]: type === "checkbox" ? checked : value,
    };
    setFormData(newFormData);
  };

  const handleMultiSelectChange = (name, newValue) => {
    let selection = newValue;
    if (newValue.includes(ALL_MODELS_VALUE) && newValue.length > 1) {
      selection = [ALL_MODELS_VALUE];
    }
    // Removed: else if (newValue.length === 0 && name === 'target_models') {
    // Removed:   selection = [ALL_MODELS_VALUE];
    // Removed: }
    // This allows the selection to be temporarily empty.
    // handleSave will default to [ALL_MODELS_VALUE] if it's empty on save.
    setFormData((prev) => ({ ...prev, [name]: selection }));
  };

  const handleSave = () => {
    if (onSave) {
      const { type, level } = effectiveTypeAndLevel(formData, ruleData);

      if (type === "COMMAND_RULE" && !formData.command_label?.trim()) {
        alert("Command is required for command rules.");
        return;
      }

      const dataToSave = {
        ...formData,
        type,
        level,
      };
      if (type === null) delete dataToSave.type;

      if (ruleData?.id) {
        dataToSave.id = ruleData.id;
      }

      // Default to ALL_MODELS if target_models is empty
      if (
        !Array.isArray(dataToSave.target_models) ||
        dataToSave.target_models.length === 0
      ) {
        dataToSave.target_models = [ALL_MODELS_VALUE];
      }

      if (dataToSave.project_id === "") dataToSave.project_id = null;
      if (dataToSave.client_id === "") dataToSave.client_id = null;

      onSave(dataToSave);
    }
  };

  const handleDelete = () => {
    if (ruleData?.id && onDelete) {
      onDelete(ruleData.id);
    }
  };

  const handleOpenLinkerModal = () => {
    if (onOpenLinker && ruleData) {
      onOpenLinker(ruleData);
    }
  };

  // What this save will store: the stored level/type for an existing rule,
  // the derived pair for a new one.
  const effective = effectiveTypeAndLevel(formData, ruleData);
  const detectedType = displayKind(effective);
  const commandRequired = effective.type === "COMMAND_RULE";
  // The type is fixed after creation, so an existing non-command rule cannot
  // take a command label; a command rule must keep one.
  const commandLocked = !isNewRule && !commandRequired;
  const nameLocked =
    !isNewRule && LOOKED_UP_NAMES.includes(ruleData?.name || "");
  const getTypeDisplayInfo = (type) => {
    switch (type) {
      case "COMMAND":
        return {
          label: "COMMAND",
          color: theme.palette.primary.main,
          description: "Interactive slash command",
        };
      case "SYSTEM":
        return {
          label: "SYSTEM",
          color: theme.palette.error.main,
          description: "Core system prompt",
        };
      case "LEARNED":
        // A learned row with a reference_id was written by the /saverule
        // command of the /api/query endpoint, which the chat page does not
        // call; one without came from the rule bundle.
        return {
          label: "LEARNED",
          color: theme.palette.info.main,
          description: ruleData?.reference_id
            ? "Saved by /saverule on the /api/query endpoint"
            : "Learned rule shipped in the rule bundle",
        };
      default:
        return {
          label: "PROMPT",
          color: theme.palette.warning.main,
          description: "User prompt template",
        };
    }
  };

  const typeInfo = getTypeDisplayInfo(detectedType);
  const contentField = contentFieldFor(effective.type);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      scroll="paper"
    >
      <DialogTitle
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        {isNewRule
          ? "Create Rule"
          : `Edit: ${formData.name || ruleData?.name || "Rule"}`}
        <IconButton onClick={onClose} size="small">
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers>
        {modelFetchError && (
          <CollapsibleAlert severity="warning" sx={{ mb: 2 }}>
            {modelFetchError}
          </CollapsibleAlert>
        )}
        <Grid container spacing={3}>
          {/* Basic Information */}
          <Grid item xs={12}>
            <Grid container spacing={2}>
              <Grid item xs={12} sm={8}>
                <TextField
                  label="Name"
                  name="name"
                  value={formData.name || ""}
                  onChange={handleChange}
                  fullWidth
                  required
                  autoFocus={!nameLocked}
                  disabled={nameLocked}
                  helperText={
                    nameLocked
                      ? "The chat engine looks this rule up by name, so the name cannot change"
                      : "A unique and descriptive name"
                  }
                />
              </Grid>
              <Grid
                item
                xs={12}
                sm={4}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <FormControlLabel
                  control={
                    <Switch
                      name="is_active"
                      checked={formData.is_active || false}
                      onChange={handleChange}
                    />
                  }
                  label="Active"
                />
              </Grid>
            </Grid>
          </Grid>

          {/* Type Display & Command */}
          <Grid item xs={12}>
            <Grid container spacing={2} alignItems="center">
              <Grid item xs={12} sm={6}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Typography variant="body2" color="text.secondary">
                    Type:
                  </Typography>
                  <Typography
                    variant="body2"
                    sx={{
                      backgroundColor: typeInfo.color,
                      color: "white",
                      padding: "3px 8px",
                      borderRadius: "4px",
                      fontWeight: "medium",
                      fontSize: "12px",
                    }}
                  >
                    {typeInfo.label}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {typeInfo.description}
                    {!isNewRule &&
                      ` (${effective.level} / ${effective.type || "no type"}, kept on save)`}
                  </Typography>
                </Box>
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  label="Command (optional)"
                  name="command_label"
                  value={formData.command_label || ""}
                  onChange={handleChange}
                  fullWidth
                  size="small"
                  placeholder="/your-command"
                  disabled={commandLocked}
                  helperText={
                    commandLocked
                      ? "Type is fixed after creation; create a new rule to add a command"
                      : commandRequired && !isNewRule
                        ? "Required; the type is fixed after creation"
                        : commandRequired
                          ? "Required for command rules"
                          : "Leave empty for prompt templates"
                  }
                  error={commandRequired && !formData.command_label?.trim()}
                />
              </Grid>
            </Grid>
          </Grid>

          {/* Content */}
          <Grid item xs={12}>
            <TextField
              label={contentField.label}
              name="rule_text"
              value={formData.rule_text || ""}
              onChange={handleChange}
              multiline
              minRows={6}
              maxRows={20}
              fullWidth
              required
              helperText={contentField.helperText}
            />
          </Grid>

          {/* Description & Models */}
          <Grid item xs={12}>
            <TextField
              label="Description"
              name="description"
              value={formData.description || ""}
              onChange={handleChange}
              multiline
              minRows={2}
              maxRows={4}
              fullWidth
              helperText="Optional description of purpose and usage"
            />
          </Grid>

          <Grid item xs={12}>
            <Autocomplete
              multiple
              options={modelOptions}
              loading={isLoadingModels}
              value={formData.target_models || []}
              onChange={(event, newValue) =>
                handleMultiSelectChange("target_models", newValue)
              }
              getOptionLabel={(option) =>
                option === ALL_MODELS_VALUE ? "All Models" : option
              }
              isOptionEqualToValue={(option, value) => option === value}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => {
                  const { key, ...otherTagProps } = getTagProps({ index });
                  return (
                    <Chip
                      key={key || option}
                      variant="outlined"
                      label={
                        option === ALL_MODELS_VALUE ? "All Models" : option
                      }
                      {...otherTagProps}
                    />
                  );
                })
              }
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Target Models"
                  placeholder="Select models or leave empty for all"
                  helperText="Choose which models this rule applies to"
                  InputProps={{
                    ...params.InputProps,
                    endAdornment: (
                      <React.Fragment>
                        {isLoadingModels && (
                          <CircularProgress color="inherit" size={20} />
                        )}
                        {params.InputProps.endAdornment}
                      </React.Fragment>
                    ),
                  }}
                />
              )}
            />
          </Grid>
        </Grid>
      </DialogContent>
      <DialogActions sx={{ p: 2, gap: 1 }}>
        {!isNewRule && ruleData?.id && (
          <>
            <Button
              variant="outlined"
              size="small"
              startIcon={<LinkIcon />}
              onClick={handleOpenLinkerModal}
              disabled={isSaving}
            >
              Links
            </Button>
            <Button
              variant="text"
              color="error"
              size="small"
              onClick={handleDelete}
              disabled={isSaving}
            >
              Delete
            </Button>
          </>
        )}
        <Box sx={{ flexGrow: 1 }} />
        <Button onClick={onClose} disabled={isSaving}>
          Cancel
        </Button>
        <Button
          onClick={handleSave}
          variant="contained"
          disabled={
            isSaving ||
            !formData.name?.trim() ||
            !formData.rule_text?.trim() ||
            (commandRequired && !formData.command_label?.trim())
          }
        >
          {isSaving ? "Saving..." : isNewRule ? "Create" : "Save"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default RuleActionModal;
