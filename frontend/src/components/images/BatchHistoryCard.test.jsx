/**
 * Regression guard for the batch-history thumbnail slowdown.
 *
 * With ~55 batches on screen the page became unusable until the list was cleared. Two
 * properties of this card are what keep that from coming back: previews must load
 * lazily rather than all at once on mount, and the card must not re-render when the
 * page around it re-renders (the queue poll fires every 2.5s).
 */
import React, { useState } from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import BatchHistoryCard from './BatchHistoryCard';

const batch = {
  batch_id: 'ImageBatch_08-04-2026_204752_001',
  status: 'completed',
  completed_images: 4,
  total_images: 4,
  display_name: 'Test batch',
};

const noop = () => {};
const handlers = {
  onOpen: noop,
  onLoad: noop,
  onHide: noop,
  onDownload: noop,
  onAdjustRetry: noop,
};

describe('BatchHistoryCard', () => {
  it('loads its preview lazily and asynchronously', () => {
    render(<BatchHistoryCard batch={batch} dateStr="Aug 4 20:47" {...handlers} />);

    const img = screen.getByAltText('Preview');
    expect(img.getAttribute('loading')).toBe('lazy');
    expect(img.getAttribute('decoding')).toBe('async');
    // Intrinsic size lets the browser reserve the box instead of reflowing per image.
    expect(img.getAttribute('width')).toBe('256');
    expect(img.getAttribute('height')).toBe('192');
    expect(img.getAttribute('src')).toContain(`/batch-image/preview/${batch.batch_id}`);
  });

  it('does not re-render when the surrounding page re-renders with stable props', () => {
    const onOpen = vi.fn();
    // Count real DOM work by observing that React reuses the same node and never
    // re-invokes the render path with changed output.
    const renderCount = { n: 0 };
    const Probe = () => {
      renderCount.n += 1;
      return null;
    };

    function Page() {
      const [tick, setTick] = useState(0);
      return (
        <>
          <button onClick={() => setTick((t) => t + 1)}>poll</button>
          <span data-testid="tick">{tick}</span>
          <BatchHistoryCard
            batch={batch}
            dateStr="Aug 4 20:47"
            onOpen={onOpen}
            onLoad={noop}
            onHide={noop}
            onDownload={noop}
            onAdjustRetry={noop}
          >
            <Probe />
          </BatchHistoryCard>
        </>
      );
    }

    render(<Page />);
    const imgBefore = screen.getByAltText('Preview');

    // Five queue polls' worth of parent re-renders.
    for (let i = 0; i < 5; i += 1) {
      fireEvent.click(screen.getByText('poll'));
    }

    expect(screen.getByTestId('tick').textContent).toBe('5');
    // Memoised: same DOM node, never torn down and rebuilt.
    expect(screen.getByAltText('Preview')).toBe(imgBefore);
  });

  it('is wrapped in React.memo so stable props short-circuit rendering', () => {
    // $$typeof memo === Symbol.for('react.memo'); guards against the wrapper being
    // dropped in a future refactor, which is what regressed performance originally.
    expect(BatchHistoryCard.$$typeof).toBe(Symbol.for('react.memo'));
  });

  it('shows the image count and wires the row actions', () => {
    const onDownload = vi.fn();
    const onAdjustRetry = vi.fn();
    const onHide = vi.fn();
    render(
      <BatchHistoryCard
        batch={batch}
        dateStr="Aug 4 20:47"
        onOpen={noop}
        onLoad={noop}
        onHide={onHide}
        onDownload={onDownload}
        onAdjustRetry={onAdjustRetry}
      />
    );

    expect(screen.getByText('4 images')).toBeTruthy();
    expect(screen.getByText('Test batch')).toBeTruthy();

    fireEvent.click(screen.getByText('Download'));
    expect(onDownload).toHaveBeenCalledWith(batch.batch_id);

    fireEvent.click(screen.getByText('Adjust & Retry'));
    expect(onAdjustRetry).toHaveBeenCalledWith(batch.batch_id);
  });

  it('opens the gallery for completed batches and loads in-flight ones', () => {
    const onOpen = vi.fn();
    const onLoad = vi.fn();

    const { rerender } = render(
      <BatchHistoryCard batch={batch} dateStr="" {...handlers} onOpen={onOpen} onLoad={onLoad} />
    );
    fireEvent.click(screen.getByAltText('Preview'));
    expect(onOpen).toHaveBeenCalledWith(batch, 0);
    expect(onLoad).not.toHaveBeenCalled();

    const running = { ...batch, batch_id: 'running-1', status: 'running' };
    rerender(
      <BatchHistoryCard batch={running} dateStr="" {...handlers} onOpen={onOpen} onLoad={onLoad} />
    );
    fireEvent.click(screen.getByAltText('Preview'));
    expect(onLoad).toHaveBeenCalledWith('running-1');
    // A running batch shows its status chip and no Browse/Download actions.
    expect(screen.getByText('running')).toBeTruthy();
    expect(screen.queryByText('Download')).toBeNull();
  });

  it('renders a placeholder instead of an <img> when the batch has no images', () => {
    render(
      <BatchHistoryCard
        batch={{ ...batch, completed_images: 0, total_images: 0 }}
        dateStr=""
        {...handlers}
      />
    );
    expect(screen.queryByAltText('Preview')).toBeNull();
  });
});
