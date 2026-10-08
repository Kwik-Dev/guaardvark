import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import VoiceFallbackNotice from './VoiceFallbackNotice';

describe('VoiceFallbackNotice', () => {
  it('renders nothing when every line was spoken as asked', () => {
    const { container } = render(<VoiceFallbackNotice shots={[
      { id: 1, scene_number: 1, shot_number: 1, voice_record: { voice: 'bm_george', fallbacks: [] } },
      { id: 2, scene_number: 1, shot_number: 2, voice_record: null },
    ]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('names each shot whose line fell back and why', () => {
    render(<VoiceFallbackNotice shots={[
      { id: 1, scene_number: 1, shot_number: 3, voice_record: { fallbacks: [
        { kind: 'engine_fallback', message: 'Chatterbox failed (CUDA out of memory); Kokoro (af_heart) spoke this line.' },
      ] } },
    ]} />);
    expect(screen.getByText('1 line was not spoken in the voice asked for')).toBeInTheDocument();
    expect(screen.getByText(/Scene 1, shot 3: Chatterbox failed \(CUDA out of memory\)/)).toBeInTheDocument();
  });
});
