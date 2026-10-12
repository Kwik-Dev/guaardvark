// One Import button with the other ways to run it in its menu: Import (a copy;
// the source is left as it is), Preview only, and Import and move…, which is
// offered for a folder on the Guaardvark machine only (a browser cannot remove
// the files it uploaded) and always asks first.
import React, { useRef, useState } from "react";
import { Button, ButtonGroup, ListItemText, Menu, MenuItem, Tooltip } from "@mui/material";
import ArrowDropDownIcon from "@mui/icons-material/ArrowDropDown";

const ImportActionButton = ({
  label = "Import",
  disabled = false,
  disabledReason = "",
  canPreview = false,
  canMove = false,
  onImport,
  onPreview,
  onMove,
}) => {
  const anchorRef = useRef(null);
  const [open, setOpen] = useState(false);
  const run = (handler) => () => {
    setOpen(false);
    handler?.();
  };

  const group = (
    <ButtonGroup variant="contained" size="small" ref={anchorRef} disabled={disabled} aria-label="Import">
      <Button onClick={onImport} sx={{ px: 2, whiteSpace: "nowrap" }}>
        {label}
      </Button>
      <Button
        size="small"
        aria-label="More ways to import"
        aria-haspopup="menu"
        aria-expanded={open ? "true" : undefined}
        onClick={() => setOpen((v) => !v)}
        sx={{ px: 0.5, minWidth: 0 }}
      >
        <ArrowDropDownIcon fontSize="small" />
      </Button>
    </ButtonGroup>
  );

  return (
    <>
      {disabled && disabledReason ? (
        <Tooltip title={disabledReason}>
          <span style={{ display: "inline-flex" }}>{group}</span>
        </Tooltip>
      ) : (
        group
      )}
      <Menu
        anchorEl={anchorRef.current}
        open={open}
        onClose={() => setOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <MenuItem onClick={run(onImport)}>
          <ListItemText primary="Import" secondary="Copies the files; the source is left as it is" />
        </MenuItem>
        <MenuItem onClick={run(onPreview)} disabled={!canPreview}>
          <ListItemText
            primary="Preview only"
            secondary={canPreview ? "Counts what would come in; changes nothing" : "Already counted above for dropped files"}
          />
        </MenuItem>
        <MenuItem onClick={run(onMove)} disabled={!canMove}>
          <ListItemText
            primary="Import and move…"
            secondary={canMove
              ? "Removes each file from the source once it is in Files; asks first"
              : "Only for a folder on the Guaardvark machine"}
          />
        </MenuItem>
      </Menu>
    </>
  );
};

export default ImportActionButton;
