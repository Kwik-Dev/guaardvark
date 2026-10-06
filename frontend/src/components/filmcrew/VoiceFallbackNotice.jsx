// frontend/src/components/filmcrew/VoiceFallbackNotice.jsx
// Lines of a rendered production that were not spoken in the voice asked for:
// a Cast voice that is not a built-in voice, Chatterbox failing so Kokoro
// spoke, or no voiceover at all. Each shot's voice_record comes from the
// Editor (backend swarm/clients.build_voice_record).

import React from 'react';
import { Alert, Typography } from '@mui/material';

const VoiceFallbackNotice = ({ shots }) => {
  const affected = (shots || []).filter((s) => (s.voice_record?.fallbacks || []).length > 0);
  if (affected.length === 0) return null;
  return (
    <Alert severity="warning" sx={{ mb: 2 }}>
      <Typography variant="subtitle2" gutterBottom>
        {affected.length === 1
          ? '1 line was not spoken in the voice asked for'
          : `${affected.length} lines were not spoken in the voice asked for`}
      </Typography>
      {affected.map((s) => (
        <Typography key={s.id} variant="body2">
          Scene {s.scene_number}, shot {s.shot_number}:{' '}
          {s.voice_record.fallbacks.map((f) => f.message).join(' ')}
        </Typography>
      ))}
    </Alert>
  );
};

export default VoiceFallbackNotice;
