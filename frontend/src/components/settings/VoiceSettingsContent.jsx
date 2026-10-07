import React from "react";
import {
  Typography,
  Box,
  Button,
  CircularProgress,
  Grid,
  Chip,
  Tooltip,
} from "@mui/material";
import MuiAlert from "@mui/material/Alert";
import FileDownloadIcon from "@mui/icons-material/FileDownload";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import VoiceListeningSettings from "../voice/VoiceListeningSettings";

const VoiceSettingsContent = ({
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
}) => {
  const whisperCliMissing = voiceStatus && voiceStatus.whisper_installed === false;
  const pendingManual = whisperManualInstall
    || (whisperCliMissing && voiceStatus?.can_auto_install === false && voiceStatus?.manual_command
      ? {
        command: voiceStatus.manual_command,
        reason: 'Building Whisper.cpp needs cmake and a compiler. This machine asks for a sudo password, and Guaardvark cannot type that from a web page. Run the command below in a terminal on this machine, then click Install Whisper again.',
      }
      : null);
  const installTooltip = voiceStatus?.install_method === 'pkexec'
    ? 'Your desktop will ask for your password, then the build starts.'
    : 'Clones whisper.cpp and builds it. May take a minute or two.';

  return (
    <>
      {isVoiceLoading && (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
          <CircularProgress />
        </Box>
      )}

      {voiceError && (
        <MuiAlert severity="error" sx={{ mb: 2 }}>
          {voiceError}
        </MuiAlert>
      )}

      {!isVoiceLoading && !voiceError && whisperCliMissing && (
        <>
          <MuiAlert
            severity="info"
            sx={{ mb: pendingManual ? 1 : 2 }}
            action={
              <Tooltip title={installTooltip}>
                <span>
                  <Button
                    color="inherit"
                    size="small"
                    startIcon={isInstallingWhisper ? <CircularProgress size={16} color="inherit" /> : <FileDownloadIcon />}
                    onClick={installWhisperCpp}
                    disabled={isInstallingWhisper}
                  >
                    {isInstallingWhisper ? 'Building...' : 'Install Whisper'}
                  </Button>
                </span>
              </Tooltip>
            }
          >
            Speech recognition (Whisper.cpp) is not installed. Install it to enable voice input.
          </MuiAlert>
          {pendingManual && (
            <MuiAlert severity="warning" sx={{ mb: 2 }}>
              <Typography variant="body2" sx={{ mb: 1 }}>{pendingManual.reason}</Typography>
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1,
                  p: 1,
                  borderRadius: 1,
                  bgcolor: 'action.hover',
                  fontFamily: 'monospace',
                  fontSize: '0.8rem',
                  overflowX: 'auto',
                }}
              >
                <Box component="code" sx={{ flex: 1, whiteSpace: 'pre' }}>
                  {pendingManual.command}
                </Box>
                <Tooltip title="Copy command">
                  <Button
                    size="small"
                    color="inherit"
                    startIcon={<ContentCopyIcon fontSize="small" />}
                    onClick={() => onCopyWhisperCommand?.(pendingManual.command)}
                    sx={{ flexShrink: 0 }}
                  >
                    Copy
                  </Button>
                </Tooltip>
              </Box>
            </MuiAlert>
          )}
        </>
      )}

      {/* Speech model: its weights arrive only through this Install */}
      {!isVoiceLoading && !voiceError && voiceStatus && voiceStatus.speech_model_installed === false && (
        <MuiAlert
          severity="warning"
          sx={{ mb: 2 }}
          action={
            <Button
              color="inherit"
              size="small"
              startIcon={isInstallingWhisper ? <CircularProgress size={16} color="inherit" /> : <FileDownloadIcon />}
              onClick={installWhisperSpeechModel}
              disabled={isInstallingWhisper}
            >
              Install
            </Button>
          }
        >
          Install the speech model to use voice
        </MuiAlert>
      )}

      {/* FFmpeg Warning */}
      {!isVoiceLoading && !voiceError && voiceStatus && voiceStatus.ffmpeg_available === false && (
        <MuiAlert severity="warning" sx={{ mb: 2 }}>
          FFmpeg is not installed. Voice features require FFmpeg. Install it with: sudo apt install ffmpeg
        </MuiAlert>
      )}

      {/* Voice Model Installation Alert */}
      {!isVoiceLoading && !voiceError && voiceModelsStatus && voiceModelsStatus.installed_count === 0 && (
        <MuiAlert
          severity="warning"
          sx={{ mb: 2 }}
          action={
            <Button
              color="inherit"
              size="small"
              startIcon={isInstallingVoice ? <CircularProgress size={16} color="inherit" /> : <FileDownloadIcon />}
              onClick={installDefaultVoiceModel}
              disabled={isInstallingVoice}
            >
              {isInstallingVoice ? 'Installing...' : 'Install'}
            </Button>
          }
        >
          No voice models installed. Install LibriTTS (English US) to enable Text-to-Speech.
        </MuiAlert>
      )}

      {/* Show install option in header area if some but not all models installed */}
      {!isVoiceLoading && !voiceError && voiceModelsStatus && voiceModelsStatus.installed_count > 0 && !voiceModelsStatus.models?.find(m => m.voice_id === 'libritts')?.installed && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
          <Typography variant="body2" color="text.secondary">
            LibriTTS (recommended voice) is not installed.
          </Typography>
          <Chip
            label={isInstallingVoice ? 'Installing...' : 'Install LibriTTS'}
            size="small"
            color="primary"
            variant="outlined"
            icon={isInstallingVoice ? <CircularProgress size={14} /> : <FileDownloadIcon sx={{ fontSize: 16 }} />}
            onClick={installDefaultVoiceModel}
            disabled={isInstallingVoice}
            sx={{ cursor: isInstallingVoice ? 'wait' : 'pointer' }}
          />
        </Box>
      )}

      {!isVoiceLoading && !voiceError && (
        <>
          {/* Main Controls */}
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 3 }}>
            <Chip
              label="Text-to-Speech"
              color={voiceSettings.ttsEnabled ? 'primary' : 'default'}
              onClick={() => handleVoiceSettingChange('ttsEnabled', !voiceSettings.ttsEnabled)}
              variant={voiceSettings.ttsEnabled ? 'filled' : 'outlined'}
              size="small"
              sx={{
                '& .MuiChip-label': {
                  color: voiceSettings.ttsEnabled ? 'inherit' : 'text.secondary'
                }
              }}
            />
            <Chip
              label="Microphone"
              color={voiceSettings.micEnabled ? 'primary' : 'default'}
              onClick={() => handleVoiceSettingChange('micEnabled', !voiceSettings.micEnabled)}
              variant={voiceSettings.micEnabled ? 'filled' : 'outlined'}
              size="small"
              sx={{
                '& .MuiChip-label': {
                  color: voiceSettings.micEnabled ? 'inherit' : 'text.secondary'
                }
              }}
            />
            <Chip
              label="Narrate Buttons"
              color={voiceSettings.showNarrateButtons !== false ? 'primary' : 'default'}
              onClick={() => handleVoiceSettingChange('showNarrateButtons', voiceSettings.showNarrateButtons === false)}
              variant={voiceSettings.showNarrateButtons !== false ? 'filled' : 'outlined'}
              size="small"
              sx={{
                '& .MuiChip-label': {
                  color: voiceSettings.showNarrateButtons !== false ? 'inherit' : 'text.secondary'
                }
              }}
            />
          </Box>

          {/* Voice, Quality, and Audio in Grid */}
          <Grid container spacing={1.5} sx={{ mb: 3 }}>
            {/* Voice Selection */}
            <Grid item xs={12}>
              <Typography variant="body2" color="text.primary" sx={{ mb: 0.5, fontSize: '0.75rem' }}>Voice</Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', alignItems: 'center' }}>
                {availableVoices.map((voice) => {
                  const isAvailable = voice.available !== false;
                  const isSelected = voiceSettings.voice === voice.id;
                  return (
                    <Chip
                      key={voice.id}
                      label={voice.name + (isAvailable ? '' : ' (not installed)')}
                      size="small"
                      color={isSelected ? 'primary' : 'default'}
                      onClick={() => isAvailable && handleVoiceSettingChange('voice', voice.id)}
                      variant={isSelected ? 'filled' : 'outlined'}
                      disabled={!voiceSettings.ttsEnabled || !isAvailable}
                      sx={{
                        '& .MuiChip-label': {
                          color: isSelected ? 'inherit' : (isAvailable ? 'text.secondary' : 'text.disabled')
                        },
                        opacity: isAvailable ? 1 : 0.5
                      }}
                    />
                  );
                })}
                <Chip
                  label={isVoiceTestPlaying ? "..." : "Test"}
                  onClick={() => testVoice(voiceSettings.voice)}
                  disabled={!voiceSettings.ttsEnabled || isVoiceTestPlaying}
                  size="small"
                  color={isVoiceTestPlaying ? "default" : "primary"}
                  variant={isVoiceTestPlaying ? "filled" : "outlined"}
                  sx={{ ml: 0.5 }}
                />
              </Box>
            </Grid>

            {/* Quality and Audio in same row */}
            <Grid item xs={6}>
              <Typography variant="body2" color="text.primary" sx={{ mb: 0.5, fontSize: '0.75rem' }}>Quality</Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                <Chip
                  label="Low"
                  size="small"
                  color={voiceSettings.recordingQuality === 'low' ? 'primary' : 'default'}
                  onClick={() => handleVoiceSettingChange('recordingQuality', 'low')}
                  variant={voiceSettings.recordingQuality === 'low' ? 'filled' : 'outlined'}
                  sx={{
                    '& .MuiChip-label': {
                      color: voiceSettings.recordingQuality === 'low' ? 'inherit' : 'text.secondary'
                    }
                  }}
                />
                <Chip
                  label="Med"
                  size="small"
                  color={voiceSettings.recordingQuality === 'medium' ? 'primary' : 'default'}
                  onClick={() => handleVoiceSettingChange('recordingQuality', 'medium')}
                  variant={voiceSettings.recordingQuality === 'medium' ? 'filled' : 'outlined'}
                  sx={{
                    '& .MuiChip-label': {
                      color: voiceSettings.recordingQuality === 'medium' ? 'inherit' : 'text.secondary'
                    }
                  }}
                />
                <Chip
                  label="High"
                  size="small"
                  color={voiceSettings.recordingQuality === 'high' ? 'primary' : 'default'}
                  onClick={() => handleVoiceSettingChange('recordingQuality', 'high')}
                  variant={voiceSettings.recordingQuality === 'high' ? 'filled' : 'outlined'}
                  sx={{
                    '& .MuiChip-label': {
                      color: voiceSettings.recordingQuality === 'high' ? 'inherit' : 'text.secondary'
                    }
                  }}
                />
              </Box>
            </Grid>

            <Grid item xs={6}>
              <Typography variant="body2" color="text.primary" sx={{ mb: 0.5, fontSize: '0.75rem' }}>Audio</Typography>
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                <Chip
                  label="Gain"
                  size="small"
                  color={voiceSettings.autoGainControl ? 'primary' : 'default'}
                  onClick={() => handleVoiceSettingChange('autoGainControl', !voiceSettings.autoGainControl)}
                  variant={voiceSettings.autoGainControl ? 'filled' : 'outlined'}
                  sx={{
                    '& .MuiChip-label': {
                      color: voiceSettings.autoGainControl ? 'inherit' : 'text.secondary'
                    }
                  }}
                />
                <Chip
                  label="Noise"
                  size="small"
                  color={voiceSettings.noiseSuppression ? 'primary' : 'default'}
                  onClick={() => handleVoiceSettingChange('noiseSuppression', !voiceSettings.noiseSuppression)}
                  variant={voiceSettings.noiseSuppression ? 'filled' : 'outlined'}
                  sx={{
                    '& .MuiChip-label': {
                      color: voiceSettings.noiseSuppression ? 'inherit' : 'text.secondary'
                    }
                  }}
                />
                <Chip
                  label="Echo"
                  size="small"
                  color={voiceSettings.echoCancellation ? 'primary' : 'default'}
                  onClick={() => handleVoiceSettingChange('echoCancellation', !voiceSettings.echoCancellation)}
                  variant={voiceSettings.echoCancellation ? 'filled' : 'outlined'}
                  sx={{
                    '& .MuiChip-label': {
                      color: voiceSettings.echoCancellation ? 'inherit' : 'text.secondary'
                    }
                  }}
                />
              </Box>
            </Grid>
          </Grid>

          {/* Listening: mic button mode, speech detection, wake phrase (shared with the Voice page) */}
          <VoiceListeningSettings
            settings={voiceSettings}
            onChange={handleVoiceSettingChange}
            systemName={systemName}
          />
        </>
      )}
    </>
  );
};

export default VoiceSettingsContent;
