// frontend/src/components/modals/VoiceSettingsModal.jsx
// Modal wrapping VoiceSettingsContent for compact SettingsPage

import React from "react";
import { Dialog, DialogTitle, DialogContent } from "@mui/material";
import VoiceSettingsContent from "../settings/VoiceSettingsContent";

const VoiceSettingsModal = ({
  open,
  onClose,
  voiceSettings,
  availableVoices,
  voiceStatus,
  voiceError,
  isVoiceLoading,
  isVoiceTestPlaying,
  isInstallingVoice,
  isInstallingWhisper,
  voiceModelsStatus,
  handleVoiceSettingChange,
  installWhisperCpp,
  installWhisperSpeechModel,
  installDefaultVoiceModel,
  testVoice,
  systemName,
  whisperManualInstall,
  onCopyWhisperCommand,
  onManageVoiceModels,
  showMessage,
}) => {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Voice Settings</DialogTitle>
      <DialogContent dividers>
        <VoiceSettingsContent
          voiceSettings={voiceSettings}
          availableVoices={availableVoices}
          voiceStatus={voiceStatus}
          voiceError={voiceError}
          isVoiceLoading={isVoiceLoading}
          isVoiceTestPlaying={isVoiceTestPlaying}
          isInstallingVoice={isInstallingVoice}
          isInstallingWhisper={isInstallingWhisper}
          voiceModelsStatus={voiceModelsStatus}
          handleVoiceSettingChange={handleVoiceSettingChange}
          installWhisperCpp={installWhisperCpp}
          installWhisperSpeechModel={installWhisperSpeechModel}
          installDefaultVoiceModel={installDefaultVoiceModel}
          testVoice={testVoice}
          systemName={systemName}
          whisperManualInstall={whisperManualInstall}
          onCopyWhisperCommand={onCopyWhisperCommand}
          onManageVoiceModels={onManageVoiceModels}
          showMessage={showMessage}
        />
      </DialogContent>
    </Dialog>
  );
};

export default VoiceSettingsModal;
