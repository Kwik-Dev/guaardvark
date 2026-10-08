import { useEffect } from 'react';
import { installFileDropGuard } from '../utils/fileDropGuard';

/** Keeps a file dropped outside every drop zone from opening in the tab. */
export default function useFileDropGuard() {
  useEffect(() => installFileDropGuard(window), []);
}
