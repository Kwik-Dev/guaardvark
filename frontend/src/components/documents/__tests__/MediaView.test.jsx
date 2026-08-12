import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import MediaView from '../MediaView';

const theme = createTheme();

const renderMedia = (ui) => render(<ThemeProvider theme={theme}>{ui}</ThemeProvider>);

const mixedItems = {
  folders: [{ id: 1, name: 'docs', path: '/x/docs' }],
  files: [
    { id: 2, filename: 'shot.png', path: '/x/shot.png' },
    { id: 3, filename: 'notes.pdf', path: '/x/notes.pdf' },
  ],
};

describe('MediaView', () => {
  it('shows folders and non-media files alongside images', () => {
    renderMedia(<MediaView items={mixedItems} />);
    expect(screen.getByText('notes.pdf')).toBeInTheDocument();
    expect(screen.getByText('docs')).toBeInTheDocument();
  });

  it('navigates into a folder from the gallery contents row', () => {
    const onNavigateToPath = vi.fn();
    renderMedia(<MediaView items={mixedItems} onNavigateToPath={onNavigateToPath} />);
    fireEvent.click(screen.getByText('docs'));
    expect(onNavigateToPath).toHaveBeenCalledWith('/x/docs');
  });

  it('opens a non-media file from the gallery contents row', () => {
    const onFileOpen = vi.fn();
    renderMedia(<MediaView items={mixedItems} onFileOpen={onFileOpen} />);
    fireEvent.click(screen.getByText('notes.pdf'));
    expect(onFileOpen).toHaveBeenCalled();
    const [, file] = onFileOpen.mock.calls[0];
    expect(file.filename).toBe('notes.pdf');
  });

  it('does not claim the folder is empty when only non-media items exist', () => {
    renderMedia(
      <MediaView
        items={{
          folders: [{ id: 1, name: 'docs', path: '/x/docs' }],
          files: [{ id: 3, filename: 'notes.pdf', path: '/x/notes.pdf' }],
        }}
      />,
    );
    expect(screen.queryByText('No media files in this folder')).not.toBeInTheDocument();
    expect(screen.getByText('Select a file')).toBeInTheDocument();
    expect(screen.getByText('notes.pdf')).toBeInTheDocument();
  });
});
