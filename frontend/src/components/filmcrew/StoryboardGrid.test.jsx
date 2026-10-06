import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import StoryboardGrid from './StoryboardGrid';

describe('StoryboardGrid', () => {
  const mockShots = [
    { id: 1, scene_number: 1, shot_number: 1, description: 'Close up of hero', approved: false }
  ];

  it('renders "Approve & Render" button only when stage is awaiting_approval', () => {
    const { rerender } = render(
      <StoryboardGrid currentStage="storyboard_gen" shots={mockShots} />
    );
    expect(screen.queryByText(/Approve & Render/i)).toBeNull();

    rerender(<StoryboardGrid currentStage="awaiting_approval" shots={mockShots} />);
    expect(screen.getByText(/Approve & Render/i)).toBeDefined();
  });

  it('shows the curator verdict with its reason and tells its tick from a person\'s', () => {
    const shots = [
      {
        id: 1, scene_number: 1, shot_number: 1, description: 'Wide', approved: true,
        approved_by: 'curator', curator_advice: { verdict: 'approve', reason: 'clean frame', confidence: 90 },
      },
      {
        id: 2, scene_number: 1, shot_number: 2, description: 'Close', approved: false,
        approved_by: null, curator_advice: { verdict: 'flag', reason: 'distorted face', confidence: 20 },
      },
      { id: 3, scene_number: 1, shot_number: 3, description: 'Two shot', approved: true, approved_by: 'person' },
    ];
    render(<StoryboardGrid currentStage="awaiting_approval" shots={shots} />);
    expect(screen.getByText('Curator: approved — clean frame')).toBeDefined();
    expect(screen.getByText('Curator: flagged — distorted face')).toBeDefined();
    expect(screen.getByText('Pre-ticked')).toBeDefined();
    expect(screen.getByText('Approved')).toBeDefined();
  });

  it('renders in one click when the curator flagged nothing', () => {
    const onApproveAll = vi.fn();
    render(<StoryboardGrid currentStage="awaiting_approval" shots={mockShots} onApproveAll={onApproveAll} />);
    fireEvent.click(screen.getByText(/Approve & Render/i));
    expect(onApproveAll).toHaveBeenCalledTimes(1);
    expect(onApproveAll).toHaveBeenCalledWith();
  });

  it('asks before rendering shots the curator flagged', () => {
    const onApproveAll = vi.fn();
    const shots = [
      { id: 1, scene_number: 1, shot_number: 1, description: 'Wide', approved: true, approved_by: 'curator',
        curator_advice: { verdict: 'approve', reason: 'clean frame', confidence: 90 } },
      { id: 2, scene_number: 1, shot_number: 2, description: 'Close', approved: false, approved_by: null,
        curator_advice: { verdict: 'flag', reason: 'distorted face', confidence: 20 } },
    ];
    render(<StoryboardGrid currentStage="awaiting_approval" shots={shots} onApproveAll={onApproveAll} />);
    fireEvent.click(screen.getByText(/Approve & Render/i));
    expect(onApproveAll).not.toHaveBeenCalled();
    expect(screen.getByText(/1 shot was flagged by the curator/i)).toBeDefined();
    expect(screen.getByText(/Scene 1 \/ Shot 2: distorted face/i)).toBeDefined();

    fireEvent.click(screen.getByRole('button', { name: /Render anyway/i }));
    expect(onApproveAll).toHaveBeenCalledWith({ confirmFlagged: true });
  });

  it('opens regenerate dialog when Refresh icon is clicked', () => {
    render(<StoryboardGrid currentStage="awaiting_approval" shots={mockShots} />);
    const regenBtn = screen.getByRole('button', { name: /Regenerate this shot/i });
    fireEvent.click(regenBtn);
    expect(screen.getByText(/Regenerate Shot 1.1/i)).toBeDefined();
  });
});
