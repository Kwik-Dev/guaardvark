import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

const deleteCastSubject = vi.fn(async () => ({}));
vi.mock('../../api/productionService', () => ({
  listCastLibrary: vi.fn(async () => ({
    subjects: [{ id: 5, name: 'Sage Harlow', kind: 'character', training_status: 'trained' }],
  })),
  createCastSubject: vi.fn(),
  deleteCastSubject: (...args) => deleteCastSubject(...args),
}));

import CastLibraryView from './CastLibraryView';

describe('CastLibraryView right-click', () => {
  beforeEach(() => {
    navigate.mockClear();
    deleteCastSubject.mockClear();
  });

  it('opens the studio from the card menu without the card click firing twice', async () => {
    render(<CastLibraryView />);
    fireEvent.contextMenu(await screen.findByText('Sage Harlow'), { clientX: 10, clientY: 10 });
    expect(screen.getAllByRole('menuitem').map((el) => el.textContent)).toEqual([
      'Open studio',
      'Remove from cast library',
    ]);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Open studio' }));
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith('/cast/5');
  });

  it('removes through the existing confirm', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<CastLibraryView />);
    fireEvent.contextMenu(await screen.findByText('Sage Harlow'), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole('menuitem', { name: 'Remove from cast library' }));
    expect(confirm).toHaveBeenCalled();
    await waitFor(() => expect(deleteCastSubject).toHaveBeenCalledWith(5));
    confirm.mockRestore();
  });
});
