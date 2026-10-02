// React state for an edit form whose server record can change while it is
// open. See utils/serverFormSync.js for the merge rules; this hook only holds
// the synced form in state and exposes the operations a page needs.
//
//   reset(serverValues)           start over from a server copy (first load,
//                                 or a different record)
//   syncFromServer(values, opts)  fold in a later server copy without
//                                 touching edits; opts.overwrite as in
//                                 mergeServerValues
//   setField(key, value)          an input changed
//   markSaved(sentValues)         a save of those values succeeded
//   resolve('server'|'mine', keys?)  answer the conflict notice
import { useCallback, useMemo, useState } from 'react';

import {
  createFormSync,
  dirtyFields,
  dirtyValues,
  markFieldsSaved,
  mergeServerValues,
  resolveConflicts,
  setFieldValue,
} from '../utils/serverFormSync';

const useServerSyncedForm = (initialValues) => {
  const [form, setForm] = useState(() => createFormSync(initialValues));

  const reset = useCallback((serverValues) => setForm(createFormSync(serverValues)), []);
  const syncFromServer = useCallback(
    (serverValues, options) => setForm((prev) => mergeServerValues(prev, serverValues, options)),
    [],
  );
  const setField = useCallback((key, value) => setForm((prev) => setFieldValue(prev, key, value)), []);
  const markSaved = useCallback((sentValues) => setForm((prev) => markFieldsSaved(prev, sentValues)), []);
  const resolve = useCallback((choice, keys) => setForm((prev) => resolveConflicts(prev, choice, keys)), []);

  const dirty = useMemo(() => dirtyFields(form), [form]);
  const changes = useMemo(() => dirtyValues(form), [form]);

  return {
    values: form.values,
    conflicts: form.conflicts,
    dirty,
    isDirty: dirty.length > 0,
    changes,
    reset,
    syncFromServer,
    setField,
    markSaved,
    resolve,
  };
};

export default useServerSyncedForm;
