// frontend/src/components/settings/ui/index.js
// The Settings page vocabulary. Shape encodes function:
//   SettingChip / ChoiceChips  state (pill)
//   ActionButton               action (filled rectangle)
//   StatusPill                 read-only status (no outline, coloured dot)
//   SettingsPanel + Cluster    layout
//   ConfirmActionDialog        the one confirmation for destructive actions
//   DashboardStrip / Tile      the read-only strip above the panels

export { default as SettingChip } from "./SettingChip";
export { default as ChoiceChips } from "./ChoiceChips";
export { default as StatusPill } from "./StatusPill";
export { default as ActionButton } from "./ActionButton";
export { default as SettingsPanel } from "./SettingsPanel";
export { Cluster, Line, Sep, Hint } from "./SettingsCluster";
export { default as ConfirmActionDialog } from "./ConfirmActionDialog";
export { default as DashboardStrip, DashboardTile } from "./DashboardStrip";
