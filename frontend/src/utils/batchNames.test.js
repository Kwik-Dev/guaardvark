import { describe, it, expect } from 'vitest';
import { fallbackBatchName } from './batchNames';

describe('fallbackBatchName', () => {
  it('reads the dated Bates stamp', () => {
    expect(fallbackBatchName('VideoBatch_04-02-2026_003')).toBe('04/02 #003');
  });

  it('reads the timestamped Bates stamp output_registration writes today', () => {
    expect(fallbackBatchName('VideoBatch_09-10-2026_133744_002')).toBe('09/10 #002');
  });

  it('drops only the prefix from anything else', () => {
    expect(fallbackBatchName('VideoBatch_custom-run')).toBe('custom-run');
    expect(fallbackBatchName('my-batch')).toBe('my-batch');
  });

  it('has a label for a missing id', () => {
    expect(fallbackBatchName('')).toBe('Batch');
    expect(fallbackBatchName(undefined)).toBe('Batch');
  });
});
