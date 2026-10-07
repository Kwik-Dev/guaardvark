// Reusable context menu component for entity pages (Clients, Websites, Projects, Tasks)
// Uses MUI Menu with anchorPosition pattern matching DocumentsContextMenu styling

import React from 'react';
import { Menu, MenuItem, Divider, ListItemIcon, ListItemText } from '@mui/material';
import CheckIcon from '@mui/icons-material/Check';

const menuStyles = {
  '& .MuiPaper-root': {
    minWidth: 180,
    boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
    borderRadius: '6px',
    border: '1px solid rgba(0,0,0,0.08)',
  },
  '& .MuiMenuItem-root': {
    fontSize: '0.8125rem',
    py: 0.6,
    px: 1.5,
    minHeight: 'auto',
  },
  '& .MuiDivider-root': {
    my: 0.5,
  },
};

/**
 * EntityContextMenu - A generic right-click context menu for entity list pages.
 *
 * @param {object|null} anchorPosition - { top, left } coordinates or null to hide
 * @param {function} onClose - called when the menu should close
 * @param {Array} actions - array of action objects:
 *   { label, onClick, icon?, dividerBefore?, disabled?, color?, checked? }
 *   `checked` (true/false) marks one choice of a set, e.g. a tier; it draws a
 *   tick in the icon column. Falsy entries are skipped, so callers can write
 *   `cond && {...}` inline.
 *
 * Pair it with hooks/useContextMenu, which supplies anchorPosition and onClose.
 */
const EntityContextMenu = ({ anchorPosition, onClose, actions = [] }) => {
  const open = Boolean(anchorPosition);
  const shown = actions.filter(Boolean);

  if (!open || shown.length === 0) return null;

  return (
    <Menu
      open={open}
      onClose={onClose}
      anchorReference="anchorPosition"
      anchorPosition={anchorPosition || { top: 0, left: 0 }}
      sx={menuStyles}
    >
      {shown.map((action, index) => {
        const items = [];

        if (action.dividerBefore) {
          items.push(<Divider key={`divider-${index}`} />);
        }

        items.push(
          <MenuItem
            key={action.label || index}
            onClick={() => {
              action.onClick?.();
              onClose();
            }}
            disabled={action.disabled}
            selected={action.checked === true}
            sx={action.color ? { color: action.color } : undefined}
          >
            {(action.icon || typeof action.checked === 'boolean') && (
              <ListItemIcon sx={action.color ? { color: action.color, minWidth: 32 } : { minWidth: 32 }}>
                {typeof action.checked === 'boolean'
                  ? action.checked && <CheckIcon fontSize="small" />
                  : action.icon}
              </ListItemIcon>
            )}
            {action.icon || typeof action.checked === 'boolean' ? (
              <ListItemText primaryTypographyProps={{ fontSize: '0.8125rem' }}>
                {action.label}
              </ListItemText>
            ) : (
              action.label
            )}
          </MenuItem>
        );

        return items;
      })}
    </Menu>
  );
};

export default EntityContextMenu;
