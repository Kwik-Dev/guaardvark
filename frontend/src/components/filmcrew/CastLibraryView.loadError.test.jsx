import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const listCastLibrary = vi.fn();
vi.mock('../../api/productionService', () => ({
  listCastLibrary: (...args) => listCastLibrary(...args),
  createCastSubject: vi.fn(),
  deleteCastSubject: vi.fn(),
}));

import CastLibraryView from './CastLibraryView';

describe('CastLibraryView load failure', () => {
  it('shows the reason with Retry instead of the empty-library text', async () => {
    const err = Object.assign(new Error('Request failed with status code 503'), {
      response: { data: { error: 'Task queue is not running' } },
    });
    listCastLibrary.mockRejectedValueOnce(err);
    listCastLibrary.mockResolvedValueOnce({ subjects: [{ id: 5, name: 'Sage Harlow', kind: 'character' }] });
    render(<MemoryRouter><CastLibraryView /></MemoryRouter>);

    expect(
      await screen.findByText('The cast library could not be loaded: Task queue is not running'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No subjects in the library yet/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Sage Harlow')).toBeInTheDocument();
  });
});
