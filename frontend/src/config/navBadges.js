// Live counts for the badge keys that catalog entries name (`badge: "..."`).
// A distribution replaces this file the way it replaces brand.jsx. It is a
// separate file because a brand's hook will import the app store and API
// services, and those import brand.jsx — putting the hook on the brand object
// would make that a cycle. The hook is called once per render of the
// navigation and returns { badgeKey: count }.
import { useEffect, useMemo, useState } from "react";
import { selfImprovementService } from "../api/selfImprovementService";

// The pending-fixes list is a plain read (no key needed from the browser);
// a fix changes state only when a person acts on it, so a slow poll is enough.
const PENDING_FIXES_POLL_MS = 120000;

export const useNavBadgeCounts = () => {
  const [pendingFixes, setPendingFixes] = useState(0);

  useEffect(() => {
    let alive = true;
    const read = () =>
      selfImprovementService
        .countWaitingFixes()
        .then((res) => {
          if (alive) setPendingFixes(Number(res?.data?.waiting) || 0);
        })
        .catch(() => {});
    read();
    const timer = setInterval(read, PENDING_FIXES_POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  return useMemo(() => ({ pendingFixes }), [pendingFixes]);
};
