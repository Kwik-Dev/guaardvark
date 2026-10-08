// frontend/src/hooks/useContextMenu.js
// State for a right-click menu, shaped for components/common/EntityContextMenu.
//
//   const menu = useContextMenu();
//   <Box onContextMenu={(e) => menu.open(e, item)} />
//   <EntityContextMenu anchorPosition={menu.anchorPosition} onClose={menu.close}
//                      actions={menu.payload ? actionsFor(menu.payload) : []} />
//
// The browser's own menu is left alone where people rely on it: text fields,
// links, selected text, anything marked data-native-contextmenu (code
// editors, canvases), and dialogs or popovers the owner rendered in a portal.

import { useCallback, useState } from "react";

const NATIVE_TARGETS =
  'input, textarea, select, [contenteditable=""], [contenteditable="true"], a[href], [data-native-contextmenu]';

export function prefersNativeMenu(event) {
  const target = event?.target;
  if (target && typeof target.closest === "function" && target.closest(NATIVE_TARGETS)) {
    return true;
  }
  // React bubbles events out of portals (dialogs, popovers) to their owner; a
  // right-click inside an owner's dialog is not a right-click on the owner.
  const host = event?.currentTarget;
  if (host && target && typeof host.contains === "function" && !host.contains(target)) {
    return true;
  }
  const selection = typeof window !== "undefined" && window.getSelection ? window.getSelection() : null;
  if (selection && !selection.isCollapsed && selection.toString().trim()) {
    const node = selection.anchorNode;
    if (!host || typeof host.contains !== "function" || (node && host.contains(node))) return true;
  }
  return false;
}

export function menuPosition(event) {
  // The keyboard menu key fires contextmenu at 0,0; anchor to the element instead.
  if ((event.clientX || event.clientY) || !event.currentTarget?.getBoundingClientRect) {
    return { top: event.clientY, left: event.clientX };
  }
  const rect = event.currentTarget.getBoundingClientRect();
  return { top: Math.round(rect.top + Math.min(rect.height, 32) / 2), left: Math.round(rect.left + 16) };
}

export default function useContextMenu() {
  const [menu, setMenu] = useState(null);

  const open = useCallback((event, payload = null) => {
    if (prefersNativeMenu(event)) return false;
    event.preventDefault();
    event.stopPropagation();
    setMenu({ anchorPosition: menuPosition(event), payload });
    return true;
  }, []);

  const close = useCallback(() => setMenu(null), []);

  return {
    anchorPosition: menu ? menu.anchorPosition : null,
    payload: menu ? menu.payload : null,
    isOpen: Boolean(menu),
    open,
    close,
  };
}
