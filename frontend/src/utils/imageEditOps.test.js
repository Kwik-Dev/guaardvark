import { describe, it, expect } from 'vitest';
import { cropRectFromOperations, fitScale } from './imageEditOps';

describe('cropRectFromOperations', () => {
  it('is null with no crop applied', () => {
    expect(cropRectFromOperations([])).toBeNull();
    expect(cropRectFromOperations([{ type: 'rotate', angle: 90 }])).toBeNull();
    expect(cropRectFromOperations(undefined)).toBeNull();
  });

  it('shows the region a crop keeps', () => {
    const ops = [{ type: 'crop', x: 10, y: 20, width: 300, height: 200, unit: 'px' }];
    expect(cropRectFromOperations(ops)).toEqual({ x: 10, y: 20, width: 300, height: 200 });
  });

  it('offsets a second crop by the first, as the backend applies them in order', () => {
    const ops = [
      { type: 'crop', x: 10, y: 20, width: 300, height: 200, unit: 'px' },
      { type: 'flip', direction: 'horizontal' },
      { type: 'crop', x: 5, y: 5, width: 100, height: 50, unit: 'px' },
    ];
    expect(cropRectFromOperations(ops)).toEqual({ x: 15, y: 25, width: 100, height: 50 });
  });

  it('restores the earlier region when the last crop is undone', () => {
    const ops = [
      { type: 'crop', x: 10, y: 20, width: 300, height: 200, unit: 'px' },
      { type: 'crop', x: 5, y: 5, width: 100, height: 50, unit: 'px' },
    ];
    expect(cropRectFromOperations(ops.slice(0, -1))).toEqual({ x: 10, y: 20, width: 300, height: 200 });
    expect(cropRectFromOperations(ops.slice(0, -2))).toBeNull();
  });
});

describe('fitScale', () => {
  it('never enlarges', () => {
    expect(fitScale(100, 100, 2000, 2000)).toBe(1);
  });

  it('fits the wider side within 90% of the viewport width', () => {
    expect(fitScale(1800, 100, 1000, 2000)).toBeCloseTo(0.5);
  });

  it('fits the taller side within the viewport minus the toolbar', () => {
    expect(fitScale(100, 1052, 2000, 578)).toBeCloseTo(0.5);
  });

  it('is 1 for an empty region', () => {
    expect(fitScale(0, 0, 1000, 1000)).toBe(1);
  });
});
