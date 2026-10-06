// frontend/src/pages/StickyNotesPage.jsx
// Sticky notes board. Double-click a title to rename it. Close parks the note in
// the closed-notes drawer. Minimize hides the body and keeps its HTML.

import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  Box,
  Alert as MuiAlert,
  Badge,
  Paper,
  Typography,
  Tooltip,
  IconButton,
  useTheme,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Divider,
  InputBase,
  TextField,
  Fade,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
} from "@mui/material";
import ReactGridLayout from "react-grid-layout/legacy";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

import {
  ViewModule,
  ViewComfy,
  ViewList,
  FormatBold,
  FormatItalic,
  FormatUnderlined,
  InsertLink,
  Add,
  Close,
  Dashboard as DashboardIcon,
  StickyNote2,
  PushPin,
  PushPinOutlined,
  ContentCopy,
  ContentPaste,
  SelectAll,
  Delete,
  Edit as EditIcon,
  Search as SearchIcon,
  CloudDone,
  CloudOff,
  History as HistoryIcon,
} from "@mui/icons-material";

import { useNavigate } from "react-router-dom";
import PageLayout from "../components/layout/PageLayout";
import ClosedNotesDrawer from "../components/notes/ClosedNotesDrawer";
import { useLayout, useDashboardWidth } from "../contexts/LayoutContext";
import { ContextualLoader } from "../components/common/LoadingStates";

const LAYOUT_MODES = ["normal", "compact", "collapsed"];
const LAYOUT_MODE_LABELS = {
  normal: "Normal",
  compact: "Compact",
  collapsed: "Collapsed",
};
const LAYOUT_MODE_ICONS = {
  normal: ViewModule,
  compact: ViewComfy,
  collapsed: ViewList,
};

// The drag placeholder is the grid cell, so a minimized note has to occupy
// the header bar. Row math matches react-grid-layout's pixel height:
// rows * rowHeight + (rows - 1) * margin. 40px is the note header.
function minimizedBarRows(rowHeight, margin) {
  const target = 40;
  let rows = 1;
  while (rows < 12) {
    const px = Math.round(rowHeight * rows + Math.max(0, rows - 1) * margin);
    if (px >= target) return rows;
    rows += 1;
  }
  return rows;
}

const NOTE_COLORS = [
  "rgba(0, 128, 128, 0.15)",   // teal glass (primary)
  "rgba(30, 30, 30, 0.95)",    // dark carbon
  "rgba(138, 155, 174, 0.2)",  // steel glass
  "rgba(0, 229, 255, 0.12)",   // neon cyan glass
  "rgba(206, 147, 216, 0.15)", // magenta glass (secondary)
  "rgba(255, 255, 255, 0.08)", // frosted glass
  "rgba(0, 102, 102, 0.3)",    // deep teal
  "rgba(40, 40, 40, 0.9)",     // charcoal
];

// YIQ contrast helper (same formula as DashboardPage / DashboardCardWrapper)
const getContrastColor = (bgColor) => {
  if (!bgColor) return "rgba(0, 0, 0, 0.87)";
  // Handle rgba() strings
  const rgbaMatch = bgColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (rgbaMatch) {
    const r = parseInt(rgbaMatch[1], 10);
    const g = parseInt(rgbaMatch[2], 10);
    const b = parseInt(rgbaMatch[3], 10);
    const yiq = (r * 299 + g * 587 + b * 114) / 1000;
    return yiq > 186 ? "rgba(0, 0, 0, 0.87)" : "rgba(255, 255, 255, 0.95)";
  }
  // Handle hex strings
  let hex = bgColor.replace("#", "");
  if (hex.length === 3)
    hex = hex.split("").map((h) => h + h).join("");
  const r = parseInt(hex.substring(0, 2), 16);
  const g = parseInt(hex.substring(2, 4), 16);
  const b = parseInt(hex.substring(4, 6), 16);
  const yiq = (r * 299 + g * 587 + b * 114) / 1000;
  return yiq > 186 ? "rgba(0, 0, 0, 0.87)" : "rgba(255, 255, 255, 0.95)";
};

// ─── Inline StickyNote component ────────────────────────────────────────────

const StickyNote = React.memo(
  ({
    _noteId,
    title,
    content,
    color,
    textColor,
    isMinimized,
    isPinned,
    onToggleMinimize,
    onClose,
    onTitleChange,
    onColorChange,
    onContentChange,
    _onDeleteRequest,
    onFormat,
    onInsertLink,
    theme,
    noteRef,
  }) => {
    const colorInputRef = useRef(null);
    const contentRef = useRef(null);
    const contentPropRef = useRef(content);
    const titleCancelRef = useRef(false);
    const [lastClickTime, setLastClickTime] = useState(0);
    const [clickCount, setClickCount] = useState(0);
    const clickTimeoutRef = useRef(null);
    const [editingTitle, setEditingTitle] = useState(false);
    const [titleDraft, setTitleDraft] = useState(title || "");
    contentPropRef.current = content;

    // Keep the editor mounted across minimize. Write props into an empty node
    // (first mount, or a remount after collapsed layout) and follow external
    // updates only while the caret is not in the note.
    useEffect(() => {
      const el = contentRef.current;
      if (!el) return;
      const next = content || "";
      // Skip while equal so typing does not reset the caret. Undo and reopen
      // change `content` from outside and must rewrite the editor.
      if (el.innerHTML !== next) el.innerHTML = next;
    }, [content]);

    useEffect(() => {
      if (!editingTitle) setTitleDraft(title || "");
    }, [title, editingTitle]);

    useEffect(() => {
      return () => {
        if (clickTimeoutRef.current) clearTimeout(clickTimeoutRef.current);
      };
    }, []);

    // Double-click detection on header (same pattern as DashboardCardWrapper)
    const handleMouseDown = useCallback(
      (_e) => {
        const now = Date.now();
        const diff = now - lastClickTime;

        if (clickTimeoutRef.current) {
          clearTimeout(clickTimeoutRef.current);
          clickTimeoutRef.current = null;
        }

        if (diff < 500 && clickCount === 1) {
          setClickCount(0);
          setLastClickTime(0);
          if (onToggleMinimize) onToggleMinimize();
        } else {
          setLastClickTime(now);
          setClickCount(1);
          clickTimeoutRef.current = setTimeout(() => {
            setClickCount(0);
            setLastClickTime(0);
          }, 500);
        }
      },
      [lastClickTime, clickCount, onToggleMinimize],
    );

    // Ignore a disconnected node, and ignore an unfocused editor that just
    // became empty while state still holds text. That is the minimize wipe.
    // A focused editor may clear the note on purpose.
    const publishContent = useCallback(() => {
      const el = contentRef.current;
      if (!el || !el.isConnected) return;
      const html = el.innerHTML;
      const stored = contentPropRef.current || "";
      const focused = document.activeElement === el;
      if (!html && stored && !focused) return;
      if (html === stored) return;
      onContentChange(html);
    }, [onContentChange]);

    const commitTitle = useCallback(() => {
      setEditingTitle(false);
      const next = titleDraft;
      if (next !== (title || "")) onTitleChange(next);
    }, [titleDraft, title, onTitleChange]);

    const cancelTitle = useCallback(() => {
      titleCancelRef.current = true;
      setTitleDraft(title || "");
      setEditingTitle(false);
    }, [title]);


    const dividerColor = "rgba(255,255,255,0.08)";
    const placeholderColor = "rgba(255,255,255,0.2)";

    return (
      <Paper
        elevation={3}
        className={`draggable-card ${isMinimized ? "minimized" : ""}`}
        sx={{
          display: "flex",
          flexDirection: "column",
          height: isMinimized ? "auto" : "100%",
          minHeight: isMinimized ? 0 : "120px",
          overflow: "hidden",
          borderRadius: "8px",
          backgroundColor: color,
          backdropFilter: "blur(12px)",
          border: `1px solid rgba(255,255,255,0.06)`,
          color: textColor,
          transition: theme.transitions.create(["height", "min-height"], {
            duration: theme.transitions.duration.standard,
          }),
        }}
      >
        {/* ── Header — drag handle + title (always visible) ──────── */}
        <Box
          className="note-header"
          onMouseDown={handleMouseDown}
          sx={{
            display: "flex",
            alignItems: "center",
            pl: 1,
            pr: 2,
            minHeight: "40px",
            cursor: "grab",
            userSelect: "none",
            "&:active": { cursor: "grabbing" },
            "&:hover": {
              backgroundColor: "rgba(255,255,255,0.04)",
              borderRadius: "8px 8px 0 0",
            },
          }}
        >
          {/* Pin indicator */}
          {isPinned && (
            <PushPin sx={{ fontSize: 14, color: textColor, opacity: 0.5, mr: 0.5 }} />
          )}

          {/* Title fills the bar; only the text itself renames. The empty
              stretch of the bar still drags and double-clicks to minimize. */}
          <Box sx={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center" }}>
            {editingTitle ? (
              <InputBase
                autoFocus
                className="non-draggable"
                value={titleDraft}
                placeholder="Untitled"
                onMouseDown={(e) => e.stopPropagation()}
                onChange={(e) => setTitleDraft(e.target.value)}
                onBlur={() => {
                  if (titleCancelRef.current) {
                    titleCancelRef.current = false;
                    return;
                  }
                  commitTitle();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    e.currentTarget.blur();
                  } else if (e.key === "Escape") {
                    e.preventDefault();
                    e.stopPropagation();
                    cancelTitle();
                  }
                }}
                onFocus={(e) => e.target.select()}
                sx={{
                  flex: 1,
                  fontSize: "0.77rem",
                  fontWeight: 600,
                  color: textColor,
                  "& input": { p: 0, color: "inherit", userSelect: "text" },
                }}
              />
            ) : (
              <Typography
                className="non-draggable"
                data-note-title=""
                onMouseDown={(e) => e.stopPropagation()}
                onDoubleClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setTitleDraft(title || "");
                  setEditingTitle(true);
                }}
                sx={{
                  maxWidth: "100%",
                  fontWeight: 600,
                  fontSize: "0.77rem",
                  color: textColor,
                  overflow: "hidden",
                  whiteSpace: "nowrap",
                  textOverflow: "ellipsis",
                  cursor: "text",
                  opacity: title ? 1 : 0.3,
                  fontStyle: title ? "normal" : "italic",
                }}
              >
                {title || "Untitled"}
              </Typography>
            )}
          </Box>

          {/* Color picker dot — matches DashboardCardWrapper (8x8) */}
          <Box sx={{ position: "relative", ml: 0.5 }}>
            <Tooltip title="Change color">
              <IconButton
                onClick={() => colorInputRef.current?.click()}
                className="non-draggable"
                sx={{
                  width: 8,
                  height: 8,
                  minWidth: 8,
                  minHeight: 8,
                  p: 0,
                  borderRadius: "50%",
                  backgroundColor: color,
                  border: `1px solid ${textColor}`,
                  transition: "all 0.2s ease",
                  "&:hover": {
                    transform: "scale(1.3)",
                    boxShadow: `0 0 3px ${color}`,
                  },
                }}
              >
                <Box
                  sx={{
                    width: 2,
                    height: 2,
                    borderRadius: "50%",
                    backgroundColor: textColor,
                  }}
                />
              </IconButton>
            </Tooltip>
            <input
              ref={colorInputRef}
              type="color"
              value={color.startsWith("rgba") ? "#1e1e1e" : color}
              onChange={(e) => onColorChange(e.target.value)}
              style={{
                position: "absolute",
                opacity: 0,
                pointerEvents: "none",
                width: 1,
                height: 1,
              }}
            />
          </Box>

          {/* Close parks the note. The button sits clear of the corner resize handle. */}
          <Tooltip title="Close">
            <IconButton
              aria-label="Close note"
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onClose();
              }}
              className="non-draggable"
              size="small"
              sx={{
                width: 20,
                height: 20,
                p: 0,
                ml: 0.5,
                color: textColor,
                opacity: 0.4,
                "&:hover": { opacity: 1, backgroundColor: "rgba(255,0,0,0.1)" },
              }}
            >
              <Close sx={{ fontSize: 16 }} />
            </IconButton>
          </Tooltip>
        </Box>

        {/* Body stays mounted while minimized so its HTML is not discarded. */}
        <Box
          sx={{
            display: isMinimized ? "none" : "flex",
            flexDirection: "column",
            flexGrow: 1,
            minHeight: 0,
          }}
        >
            {/* Formatting toolbar */}
            <Box
              sx={{
                display: "flex",
                gap: 0.25,
                px: 1,
                py: 0.25,
                borderTop: `1px solid ${dividerColor}`,
                borderBottom: `1px solid ${dividerColor}`,
              }}
            >
              {[
                { cmd: "bold", icon: <FormatBold sx={{ fontSize: 14 }} />, tip: "Bold (Ctrl+B)" },
                { cmd: "italic", icon: <FormatItalic sx={{ fontSize: 14 }} />, tip: "Italic (Ctrl+I)" },
                { cmd: "underline", icon: <FormatUnderlined sx={{ fontSize: 14 }} />, tip: "Underline (Ctrl+U)" },
              ].map(({ cmd, icon, tip }) => (
                <Tooltip key={cmd} title={tip}>
                  <IconButton
                    onMouseDown={(e) => {
                      e.preventDefault();
                      onFormat(cmd);
                    }}
                    className="non-draggable"
                    size="small"
                    sx={{
                      width: 22,
                      height: 22,
                      p: 0,
                      color: textColor,
                      opacity: 0.5,
                      "&:hover": { opacity: 1 },
                    }}
                  >
                    {icon}
                  </IconButton>
                </Tooltip>
              ))}
              <Tooltip title="Insert Link">
                <IconButton
                  onMouseDown={(e) => {
                    e.preventDefault();
                    onInsertLink();
                  }}
                  className="non-draggable"
                  size="small"
                  sx={{
                    width: 22,
                    height: 22,
                    p: 0,
                    color: textColor,
                    opacity: 0.5,
                    "&:hover": { opacity: 1 },
                  }}
                >
                  <InsertLink sx={{ fontSize: 14 }} />
                </IconButton>
              </Tooltip>
            </Box>

            {/* Editable content */}
            <Box
              ref={(el) => {
                contentRef.current = el;
                if (noteRef) noteRef(el);
                if (el && !el.innerHTML && contentPropRef.current) {
                  el.innerHTML = contentPropRef.current;
                }
              }}
              className="note-content non-draggable"
              contentEditable
              suppressContentEditableWarning
              onBlur={publishContent}
              onInput={publishContent}
              sx={{
                flexGrow: 1,
                p: 1,
                overflow: "auto",
                outline: "none",
                fontSize: "0.85rem",
                lineHeight: 1.5,
                color: textColor,
                cursor: "text",
                minHeight: 60,
                "& a": {
                  color: theme.palette.primary.light,
                  textDecoration: "underline",
                },
                "&:empty::before": {
                  content: '"Type your note..."',
                  color: placeholderColor,
                  fontStyle: "italic",
                },
              }}
            />
        </Box>
      </Paper>
    );
  },
);

StickyNote.displayName = "StickyNote";

// ─── Main page component ────────────────────────────────────────────────────

const StickyNotesPage = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const { gridSettings } = useLayout();
  const dashboardWidth = useDashboardWidth();

  const {
    CONTAINER_PADDING_PX,
    CARD_MARGIN_PX,
    COLS_COUNT,
    ROW_HEIGHT_PX,
    cardMinGridW,
    cardGridW,
    cardGridH,
  } = gridSettings;

  const [initialStateLoaded, setInitialStateLoaded] = useState(false);
  const [layoutError, setLayoutError] = useState(null);
  const [notes, setNotes] = useState({});
  const [noteColors, setNoteColors] = useState({});
  const [minimizedCards, setMinimizedCards] = useState({});
  const [_cardZIndex, setCardZIndex] = useState({});
  const [maxZIndex, setMaxZIndex] = useState(0);
  const [layoutMode, setLayoutMode] = useState("normal");
  const [pinnedNotes, setPinnedNotes] = useState({});
  const [closedNotes, setClosedNotes] = useState({});
  const [closedDrawerOpen, setClosedDrawerOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [contextMenu, setContextMenu] = useState(null);
  const [desktopMenu, setDesktopMenu] = useState(null);
  const [saveIndicator, setSaveIndicator] = useState(null);
  const [deleteConfirm, setDeleteConfirm] = useState(null); // { noteId, source }
  const [renameTarget, setRenameTarget] = useState(null); // { noteId, title }
  const gridContainerRef = useRef(null);
  const [gridWidth, setGridWidth] = useState(dashboardWidth);
  const isTogglingRef = useRef(false);
  // Minimized flags at drag start. The header double-click toggles the flag
  // on the second mousedown, which can land before or after onDragStart, and
  // the drag reports the bar height. Pre-flip bits are merged in so expand
  // still stores the full height.
  const dragMinRef = useRef(null);
  const draggingRef = useRef(false);
  const noteRefs = useRef({});
  const editRangeRef = useRef(null);
  const saveTimeoutRef = useRef(null);

  // Undo history (CTRL+Z)
  const undoStackRef = useRef([]);
  const MAX_UNDO = 30;

  // Refs for latest state values — avoids stale closures in saveState/debouncedSave
  const notesRef = useRef(notes);
  const noteColorsRef = useRef(noteColors);
  const minimizedCardsRef = useRef(minimizedCards);
  const pinnedNotesRef = useRef(pinnedNotes);
  const closedNotesRef = useRef(closedNotes);
  const layoutModeRef = useRef(layoutMode);
  const layoutRef = useRef(null);
  // Sync refs immediately (not via useEffect which is async)
  notesRef.current = notes;
  noteColorsRef.current = noteColors;
  minimizedCardsRef.current = minimizedCards;
  pinnedNotesRef.current = pinnedNotes;
  closedNotesRef.current = closedNotes;
  layoutModeRef.current = layoutMode;

  // ── Grid width tracking ──────────────────────────────────────────────────

  useEffect(() => {
    if (dashboardWidth > 0) setGridWidth(dashboardWidth);
  }, [dashboardWidth]);

  useEffect(() => {
    const el = gridContainerRef.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      if (w > 0) setGridWidth(w);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // ── Layout helpers ───────────────────────────────────────────────────────

  // Default note size: square (width × width in grid units)
  const makeLayoutItem = useCallback(
    (noteId, index) => ({
      i: noteId,
      x: (index % 4) * cardGridW,
      y: Math.floor(index / 4) * cardGridW,
      w: cardGridW,
      h: cardGridW,
      minW: cardMinGridW,
      isDraggable: true,
      isResizable: true,
    }),
    [cardGridW, cardMinGridW],
  );

  const [layout, setLayout] = useState([]);
  useEffect(() => { layoutRef.current = layout; }, [layout]);
  const normalLayoutRef = useRef(null);
  // Last mode this effect applied. A title edit used to rebuild the packed
  // layouts and this effect then called setLayout, which parks every note
  // on the left. Normal mode only restores when the mode actually changes.
  const appliedModeRef = useRef(null);

  // Id list only. A title or body edit keeps this string stable so the
  // packed layouts below are not rebuilt — rebuilding them pulls dragged
  // notes back to the left edge on Enter.
  const noteIdsKey = Object.keys(notes).join("\0");

  // Compact layout (derived)
  const compactLayout = useMemo(() => {
    const noteIds = noteIdsKey ? noteIdsKey.split("\0") : [];
    const compactW = Math.round(cardGridW * 0.71);
    const compactH = Math.round(cardGridH * 0.71);
    const colWidthPx = gridWidth / COLS_COUNT;
    const cardPixelW = compactW * colWidthPx;
    const cardsPerRow = Math.max(1, Math.floor(gridWidth / cardPixelW));

    return noteIds.map((id, idx) => ({
      i: id,
      x: (idx % cardsPerRow) * compactW,
      y: Math.floor(idx / cardsPerRow) * compactH,
      w: compactW,
      h: compactH,
      minW: cardMinGridW,
      isDraggable: true,
      isResizable: false,
    }));
  }, [noteIdsKey, cardGridW, cardGridH, gridWidth, COLS_COUNT, cardMinGridW]);

  // Collapsed layout (derived)
  const collapsedLayout = useMemo(() => {
    const noteIds = noteIdsKey ? noteIdsKey.split("\0") : [];
    const colWidthPx = gridWidth / COLS_COUNT;
    const barW = Math.round(300 / colWidthPx);
    const barH = Math.round(50 / ROW_HEIGHT_PX);
    const barX = Math.max(0, COLS_COUNT - barW);

    return noteIds.map((id, idx) => ({
      i: id,
      x: barX,
      y: idx * barH,
      w: barW,
      h: barH,
      minW: cardMinGridW,
      isDraggable: true,
      isResizable: false,
    }));
  }, [noteIdsKey, gridWidth, COLS_COUNT, ROW_HEIGHT_PX, cardMinGridW]);

  // ── Load saved state ─────────────────────────────────────────────────────

  useEffect(() => {
    const fetchState = async () => {
      setLayoutError(null);
      try {
        const res = await fetch("/api/state/sticky-notes");
        if (!res.ok) {
          if (res.status === 404) {
            // First visit — one default note
            const id = `note-${Date.now()}`;
            const defaultNotes = { [id]: { content: "", title: "" } };
            const defaultColors = { [id]: NOTE_COLORS[0] };
            const defaultLayout = [makeLayoutItem(id, 0)];
            setNotes(defaultNotes);
            setNoteColors(defaultColors);
            normalLayoutRef.current = defaultLayout;
            setLayout(defaultLayout);
            setLayoutMode("normal");
          } else {
            throw new Error(`${res.statusText} (${res.status})`);
          }
        } else {
          const saved = await res.json();

          if (saved.layoutMode && LAYOUT_MODES.includes(saved.layoutMode)) {
            setLayoutMode(saved.layoutMode);
          }
          if (saved.notes && typeof saved.notes === "object") {
            // Migrate: default missing title to ""
            const migrated = {};
            for (const [id, note] of Object.entries(saved.notes)) {
              migrated[id] = { title: "", ...note };
            }
            setNotes(migrated);
          }
          if (saved.noteColors && typeof saved.noteColors === "object") {
            setNoteColors(saved.noteColors);
          }
          if (saved.minimizedCards && typeof saved.minimizedCards === "object") {
            setMinimizedCards(saved.minimizedCards);
          }
          if (saved.pinnedNotes && typeof saved.pinnedNotes === "object") {
            setPinnedNotes(saved.pinnedNotes);
          }
          if (saved.closedNotes && typeof saved.closedNotes === "object") {
            setClosedNotes(saved.closedNotes);
          }

          const noteIds = Object.keys(saved.notes || {});
          if (Array.isArray(saved.layout) && saved.layout.length > 0) {
            const validLayout = saved.layout.filter((item) =>
              noteIds.includes(item.i),
            );
            noteIds.forEach((id, idx) => {
              if (!validLayout.some((item) => item.i === id)) {
                validLayout.push(makeLayoutItem(id, idx));
              }
            });
            normalLayoutRef.current = validLayout;
            setLayout(validLayout);
          } else if (noteIds.length > 0) {
            const dl = noteIds.map((id, idx) => makeLayoutItem(id, idx));
            normalLayoutRef.current = dl;
            setLayout(dl);
          }
        }
      } catch (e) {
        console.error("StickyNotes: Error fetching state:", e);
        setLayoutError(`Failed to load notes: ${e.message}. Using defaults.`);
        const id = `note-${Date.now()}`;
        setNotes({ [id]: { content: "", title: "" } });
        setNoteColors({ [id]: NOTE_COLORS[0] });
        const dl = [makeLayoutItem(id, 0)];
        normalLayoutRef.current = dl;
        setLayout(dl);
        setLayoutMode("normal");
      }
      setInitialStateLoaded(true);
    };
    fetchState();
  }, [makeLayoutItem]);

  // ── Apply layout mode ────────────────────────────────────────────────────

  useEffect(() => {
    if (!initialStateLoaded) return;
    const modeChanged = appliedModeRef.current !== layoutMode;
    appliedModeRef.current = layoutMode;
    if (layoutMode === "normal") {
      if (modeChanged) setLayout(normalLayoutRef.current || []);
      return;
    }
    isTogglingRef.current = true;
    setLayout(layoutMode === "compact" ? compactLayout : collapsedLayout);
    requestAnimationFrame(() => {
      isTogglingRef.current = false;
    });
  }, [layoutMode, initialStateLoaded, compactLayout, collapsedLayout]);

  // ── Persistence ──────────────────────────────────────────────────────────

  const saveState = useCallback(
    async (newLayout, newNoteColors, newMinimizedCards, newLayoutMode, newNotes, newPinnedNotes, newClosedNotes) => {
      setSaveIndicator("saving");
      try {
        const body = {
          notes: newNotes || notesRef.current,
          layout: normalLayoutRef.current || newLayout || layoutRef.current,
          noteColors: newNoteColors || noteColorsRef.current,
          minimizedCards: newMinimizedCards || minimizedCardsRef.current,
          pinnedNotes: newPinnedNotes || pinnedNotesRef.current,
          closedNotes: newClosedNotes === undefined ? closedNotesRef.current : newClosedNotes,
          layoutMode:
            newLayoutMode !== undefined ? newLayoutMode : layoutModeRef.current,
          lastSaved: new Date().toISOString(),
        };
        const res = await fetch("/api/state/sticky-notes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`(${res.status})`);
        setLayoutError(null);
        setSaveIndicator("saved");
        setTimeout(() => setSaveIndicator(null), 2000);
      } catch (err) {
        console.error("Failed to save sticky notes state:", err);
        setLayoutError("Failed to save notes.");
        setSaveIndicator("error");
        setTimeout(() => setSaveIndicator(null), 3000);
      }
    },
    [],
  );

  // Debounced save for content/title typing
  const debouncedSave = useCallback(
    (newNotes) => {
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = setTimeout(() => {
        saveState(null, null, null, undefined, newNotes);
      }, 500);
    },
    [saveState],
  );

  const cancelDebouncedSave = useCallback(() => {
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }
  }, []);

  // Read the live editor before minimize/close. A pending debounce must not
  // write an older copy back after the note has been parked.
  const flushNoteDom = useCallback((noteId) => {
    const current = notesRef.current;
    const note = current[noteId];
    if (!note) return current;
    const el = noteRefs.current[noteId];
    if (!el || !el.isConnected) return current;
    const html = el.innerHTML;
    if (!html && note.content && document.activeElement !== el) return current;
    if ((note.content || "") === html) return current;
    const newNotes = { ...current, [noteId]: { ...note, content: html } };
    notesRef.current = newNotes;
    setNotes(newNotes);
    return newNotes;
  }, []);

  // Push state snapshot for undo
  const pushUndo = useCallback(() => {
    const snapshot = JSON.stringify({
      notes: notesRef.current,
      noteColors: noteColorsRef.current,
      layout: normalLayoutRef.current || layoutRef.current,
      closedNotes: closedNotesRef.current,
      pinnedNotes: pinnedNotesRef.current,
      minimizedCards: minimizedCardsRef.current,
    });
    undoStackRef.current.push(snapshot);
    if (undoStackRef.current.length > MAX_UNDO) undoStackRef.current.shift();
  }, []);

  // Undo last action
  const handleUndo = useCallback(() => {
    if (undoStackRef.current.length === 0) return;
    const snapshot = JSON.parse(undoStackRef.current.pop());
    if (snapshot.notes) {
      notesRef.current = snapshot.notes;
      setNotes(snapshot.notes);
    }
    if (snapshot.noteColors) {
      noteColorsRef.current = snapshot.noteColors;
      setNoteColors(snapshot.noteColors);
    }
    if (snapshot.layout) {
      normalLayoutRef.current = snapshot.layout;
      setLayout(snapshot.layout);
    }
    if (snapshot.closedNotes) {
      closedNotesRef.current = snapshot.closedNotes;
      setClosedNotes(snapshot.closedNotes);
    }
    if (snapshot.pinnedNotes) {
      pinnedNotesRef.current = snapshot.pinnedNotes;
      setPinnedNotes(snapshot.pinnedNotes);
    }
    if (snapshot.minimizedCards) {
      minimizedCardsRef.current = snapshot.minimizedCards;
      setMinimizedCards(snapshot.minimizedCards);
    }
    saveState(
      snapshot.layout,
      snapshot.noteColors,
      snapshot.minimizedCards,
      undefined,
      snapshot.notes,
      snapshot.pinnedNotes,
      snapshot.closedNotes,
    );
  }, [saveState]);

  // Global keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e) => {
      // CTRL+Z — Undo
      if ((e.ctrlKey || e.metaKey) && e.key === "z" && !e.shiftKey) {
        e.preventDefault();
        handleUndo();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleUndo]);

  useEffect(() => {
    return () => {
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
    };
  }, []);

  // ── Search filter ─────────────────────────────────────────────────────────

  const filteredNoteIds = useMemo(() => {
    if (!searchQuery.trim()) return null;
    const q = searchQuery.toLowerCase();
    return new Set(
      Object.entries(notes)
        .filter(([, note]) => {
          const titleMatch = (note.title || "").toLowerCase().includes(q);
          const contentMatch = (note.content || "").replace(/<[^>]*>/g, "").toLowerCase().includes(q);
          return titleMatch || contentMatch;
        })
        .map(([id]) => id),
    );
  }, [notes, searchQuery]);

  // ── Event handlers ───────────────────────────────────────────────────────

  const commitGridLayout = useCallback(
    (newLayout, fromDrag) => {
      draggingRef.current = false;
      if (isTogglingRef.current) {
        dragMinRef.current = null;
        return;
      }
      const prevById = new Map((layoutRef.current || []).map((item) => [item.i, item]));
      const minimizedAtStart = fromDrag
        ? (dragMinRef.current || minimizedCardsRef.current)
        : minimizedCardsRef.current;
      dragMinRef.current = null;
      // Minimized notes are handed to the grid at bar height. Keep the stored
      // height so expand still opens the size the note had before.
      const validLayout = newLayout.filter((item) => item && item.i).map((item) => {
        const prev = prevById.get(item.i);
        if (!prev) return item;
        const next = { ...prev, x: item.x, y: item.y, w: item.w };
        if (!minimizedAtStart[item.i]) next.h = item.h;
        return next;
      });
      if (layoutMode === "normal") normalLayoutRef.current = validLayout;
      setLayout(validLayout);
      // Flush any pending debounced save to prevent content loss
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current);
        saveTimeoutRef.current = null;
      }
      // Pass notes explicitly from ref to ensure latest content is saved
      saveState(validLayout, noteColorsRef.current, minimizedCardsRef.current, undefined, notesRef.current);
    },
    [saveState, layoutMode],
  );

  const handleNoteColorChange = useCallback(
    (noteId, color) => {
      pushUndo();
      const c = { ...noteColorsRef.current, [noteId]: color };
      setNoteColors(c);
      // Flush any pending debounced save to prevent content loss
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current);
        saveTimeoutRef.current = null;
      }
      // Pass notes explicitly from ref to ensure latest content is saved
      saveState(layoutRef.current, c, minimizedCardsRef.current, undefined, notesRef.current);
    },
    [saveState, pushUndo],
  );

  const handleToggleMinimize = useCallback(
    (noteId) => {
      if (layoutModeRef.current !== "normal") return;
      cancelDebouncedSave();
      const liveNotes = flushNoteDom(noteId);
      isTogglingRef.current = true;

      const prevMin = minimizedCardsRef.current;
      const snap = { ...(dragMinRef.current || {}) };
      snap[noteId] = !!prevMin[noteId];
      dragMinRef.current = snap;
      const newMin = { ...prevMin, [noteId]: !prevMin[noteId] };
      minimizedCardsRef.current = newMin;
      setMinimizedCards(newMin);

      // Leave x/y/w/h alone. The grid renders a bar-height copy while the
      // flag is set, and the stored height is what expand opens.
      saveState(layoutRef.current, noteColorsRef.current, newMin, undefined, liveNotes);
      requestAnimationFrame(() => {
        isTogglingRef.current = false;
        if (!draggingRef.current) dragMinRef.current = null;
      });
    },
    [saveState, cancelDebouncedSave, flushNoteDom],
  );

  const handleCardClick = useCallback(
    (noteId) => {
      const z = maxZIndex + 1;
      setMaxZIndex(z);
      setCardZIndex((prev) => ({ ...prev, [noteId]: z }));
      const el = document.querySelector(`[data-card-id="${noteId}"]`);
      const gridItem = el?.closest(".react-grid-item") || el;
      if (gridItem) gridItem.style.zIndex = z;
    },
    [maxZIndex],
  );

  const handleCycleLayoutMode = useCallback(() => {
    const idx = LAYOUT_MODES.indexOf(layoutMode);
    const next = LAYOUT_MODES[(idx + 1) % LAYOUT_MODES.length];
    if (layoutMode === "normal") normalLayoutRef.current = layout;
    setLayoutMode(next);
    saveState(normalLayoutRef.current || layout, noteColors, minimizedCards, next);
  }, [layoutMode, layout, noteColors, minimizedCards, saveState]);

  // Add note
  const handleAddNote = useCallback(() => {
    const id = `note-${Date.now()}`;
    const colorIdx = Object.keys(notes).length % NOTE_COLORS.length;
    const newNotes = { ...notes, [id]: { content: "", title: "" } };
    const newColors = { ...noteColors, [id]: NOTE_COLORS[colorIdx] };
    const item = makeLayoutItem(id, Object.keys(notes).length);
    const newLayout = [...(normalLayoutRef.current || layout), item];
    normalLayoutRef.current = newLayout;

    setNotes(newNotes);
    setNoteColors(newColors);
    setLayout(newLayout);
    saveState(newLayout, newColors, minimizedCards, undefined, newNotes);
  }, [notes, noteColors, layout, minimizedCards, makeLayoutItem, saveState]);

  // Delete note from the board or from the closed list.
  const handleDeleteNote = useCallback(
    (noteId, source = "open") => {
      cancelDebouncedSave();
      pushUndo();
      if (source === "closed") {
        const { [noteId]: _removed, ...restClosed } = closedNotesRef.current;
        closedNotesRef.current = restClosed;
        setClosedNotes(restClosed);
        saveState(null, null, null, undefined, undefined, undefined, restClosed);
        return;
      }
      const { [noteId]: _, ...rest } = notesRef.current;
      const { [noteId]: __, ...restColors } = noteColorsRef.current;
      const { [noteId]: ___, ...restMin } = minimizedCardsRef.current;
      const { [noteId]: ____, ...restPinned } = pinnedNotesRef.current;
      const newLayout = (normalLayoutRef.current || layoutRef.current || []).filter(
        (i) => i.i !== noteId,
      );
      normalLayoutRef.current = newLayout;
      notesRef.current = rest;
      noteColorsRef.current = restColors;
      minimizedCardsRef.current = restMin;
      pinnedNotesRef.current = restPinned;
      setNotes(rest);
      setNoteColors(restColors);
      setMinimizedCards(restMin);
      setPinnedNotes(restPinned);
      setLayout(newLayout);
      saveState(newLayout, restColors, restMin, undefined, rest, restPinned);
    },
    [saveState, pushUndo, cancelDebouncedSave],
  );

  // Park an open note. The text moves with it into closedNotes.
  const handleCloseNote = useCallback(
    (noteId) => {
      cancelDebouncedSave();
      const liveNotes = flushNoteDom(noteId);
      const note = liveNotes[noteId];
      if (!note) return;
      pushUndo();
      const { [noteId]: _removed, ...rest } = liveNotes;
      const { [noteId]: _color, ...restColors } = noteColorsRef.current;
      const { [noteId]: _min, ...restMin } = minimizedCardsRef.current;
      const { [noteId]: _pin, ...restPinned } = pinnedNotesRef.current;
      const newLayout = (normalLayoutRef.current || layoutRef.current || []).filter(
        (i) => i.i !== noteId,
      );
      const closed = {
        title: note.title || "",
        content: note.content || "",
        color: noteColorsRef.current[noteId] || NOTE_COLORS[0],
        pinned: !!pinnedNotesRef.current[noteId],
        closedAt: new Date().toISOString(),
      };
      const nextClosed = { ...closedNotesRef.current, [noteId]: closed };
      normalLayoutRef.current = newLayout;
      layoutRef.current = newLayout;
      notesRef.current = rest;
      noteColorsRef.current = restColors;
      minimizedCardsRef.current = restMin;
      pinnedNotesRef.current = restPinned;
      closedNotesRef.current = nextClosed;
      setNotes(rest);
      setNoteColors(restColors);
      setMinimizedCards(restMin);
      setPinnedNotes(restPinned);
      setClosedNotes(nextClosed);
      if (layoutModeRef.current === "normal") setLayout(newLayout);
      saveState(newLayout, restColors, restMin, undefined, rest, restPinned, nextClosed);
    },
    [saveState, pushUndo, cancelDebouncedSave, flushNoteDom],
  );

  const handleReopenNote = useCallback(
    (noteId) => {
      cancelDebouncedSave();
      const closed = closedNotesRef.current[noteId];
      if (!closed) return;
      pushUndo();
      const { [noteId]: _removed, ...restClosed } = closedNotesRef.current;
      const newNotes = {
        ...notesRef.current,
        [noteId]: { title: closed.title || "", content: closed.content || "" },
      };
      const newColors = {
        ...noteColorsRef.current,
        [noteId]: closed.color || NOTE_COLORS[0],
      };
      const newPinned = closed.pinned
        ? { ...pinnedNotesRef.current, [noteId]: true }
        : pinnedNotesRef.current;
      const item = makeLayoutItem(noteId, Object.keys(notesRef.current).length);
      const newLayout = [...(normalLayoutRef.current || layoutRef.current || []), item];
      normalLayoutRef.current = newLayout;
      notesRef.current = newNotes;
      noteColorsRef.current = newColors;
      pinnedNotesRef.current = newPinned;
      closedNotesRef.current = restClosed;
      setNotes(newNotes);
      setNoteColors(newColors);
      setPinnedNotes(newPinned);
      setClosedNotes(restClosed);
      if (layoutModeRef.current === "normal") {
        layoutRef.current = newLayout;
        setLayout(newLayout);
      }
      saveState(
        newLayout,
        newColors,
        minimizedCardsRef.current,
        undefined,
        newNotes,
        newPinned,
        restClosed,
      );
      setClosedDrawerOpen(false);
    },
    [saveState, pushUndo, cancelDebouncedSave, makeLayoutItem],
  );

  // Content change (debounced save) — use ref to avoid stale closure
  const handleNoteContentChange = useCallback(
    (noteId, content) => {
      const current = notesRef.current;
      const note = current[noteId];
      if (!note || (note.content || "") === content) return;
      if (!content && note.content) {
        const el = noteRefs.current[noteId];
        if (!el || !el.isConnected || document.activeElement !== el) return;
      }
      pushUndo();
      const newNotes = { ...current, [noteId]: { ...note, content } };
      notesRef.current = newNotes;
      setNotes(newNotes);
      debouncedSave(newNotes);
    },
    [debouncedSave, pushUndo],
  );

  // Title change (debounced save) — use ref to avoid stale closure
  const handleNoteTitleChange = useCallback(
    (noteId, title) => {
      const current = notesRef.current;
      const note = current[noteId];
      if (!note || (note.title || "") === title) return;
      pushUndo();
      const newNotes = { ...current, [noteId]: { ...note, title } };
      notesRef.current = newNotes;
      setNotes(newNotes);
      debouncedSave(newNotes);
    },
    [debouncedSave, pushUndo],
  );

  // Duplicate note
  const handleDuplicateNote = useCallback(
    (noteId) => {
      const source = notes[noteId];
      if (!source) return;
      const id = `note-${Date.now()}`;
      const newNotes = { ...notes, [id]: { content: source.content, title: source.title || "" } };
      const newColors = { ...noteColors, [id]: noteColors[noteId] || NOTE_COLORS[0] };
      const item = makeLayoutItem(id, Object.keys(notes).length);
      const newLayout = [...(normalLayoutRef.current || layout), item];
      normalLayoutRef.current = newLayout;
      setNotes(newNotes);
      setNoteColors(newColors);
      setLayout(newLayout);
      saveState(newLayout, newColors, minimizedCards, undefined, newNotes);
    },
    [notes, noteColors, layout, minimizedCards, makeLayoutItem, saveState],
  );

  // Toggle pin
  const handleTogglePin = useCallback(
    (noteId) => {
      const newPinned = { ...pinnedNotes };
      if (newPinned[noteId]) {
        delete newPinned[noteId];
      } else {
        newPinned[noteId] = true;
      }
      setPinnedNotes(newPinned);
      saveState(null, null, null, undefined, undefined, newPinned);
    },
    [pinnedNotes, saveState],
  );

  // Format commands via execCommand
  const handleFormat = useCallback((command) => {
    document.execCommand(command, false, null);
  }, []);

  const handleInsertLink = useCallback(() => {
    const sel = window.getSelection();
    const range = sel && sel.rangeCount > 0 ? sel.getRangeAt(0) : null;
    const url = window.prompt("Enter URL:");
    if (url && range) {
      sel.removeAllRanges();
      sel.addRange(range);
      document.execCommand("createLink", false, url);
    }
  }, []);

  const selectNoteContents = useCallback((el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }, []);

  const handleSelectAllNote = useCallback((noteId) => {
    setContextMenu(null);
    requestAnimationFrame(() => {
      const el = noteRefs.current[noteId];
      if (!el) return;
      el.focus();
      selectNoteContents(el);
    });
  }, [selectNoteContents]);

  const handleCopyNote = useCallback((noteId) => {
    const el = noteRefs.current[noteId];
    if (!el) return;
    const sel = window.getSelection();
    const hasSelection = Boolean(
      sel && sel.rangeCount > 0 && !sel.isCollapsed && el.contains(sel.anchorNode),
    );
    if (!hasSelection) selectNoteContents(el);
    const copied = document.execCommand("copy");
    if (!copied) {
      const text = (hasSelection ? sel.toString() : el.innerText) || "";
      navigator.clipboard.writeText(text).catch((err) => {
        setLayoutError(`Could not copy: ${err?.message || "clipboard permission denied"}`);
      });
    }
    setContextMenu(null);
  }, [selectNoteContents]);

  const handlePasteNote = useCallback(async (noteId) => {
    const saved = editRangeRef.current;
    let text;
    try {
      text = await navigator.clipboard.readText();
    } catch (err) {
      setContextMenu(null);
      setLayoutError(`Could not paste: ${err?.message || "clipboard permission denied"}`);
      return;
    }
    setContextMenu(null);
    const el = noteRefs.current[noteId];
    if (!el) return;
    el.focus();
    const sel = window.getSelection();
    if (saved && el.contains(saved.startContainer)) {
      sel.removeAllRanges();
      sel.addRange(saved);
    }
    const inserted = document.execCommand("insertText", false, text);
    if (!inserted && text) {
      const range = sel.rangeCount > 0 ? sel.getRangeAt(0) : null;
      if (range) {
        range.deleteContents();
        range.insertNode(document.createTextNode(text));
        range.collapse(false);
      } else {
        el.appendChild(document.createTextNode(text));
      }
    }
    handleNoteContentChange(noteId, el.innerHTML);
  }, [handleNoteContentChange]);

  // ── Render ───────────────────────────────────────────────────────────────

  const LayoutModeIcon = LAYOUT_MODE_ICONS[layoutMode];
  const _isCompact = layoutMode === "compact";
  const isCollapsed = layoutMode === "collapsed";
  const deleteSource = deleteConfirm?.source === "closed" ? closedNotes : notes;
  const deleteNoteTitle = (deleteConfirm && deleteSource[deleteConfirm.noteId]?.title) || "Untitled";

  if (!initialStateLoaded) {
    return (
      <PageLayout title="Notes" variant="grid">
        <Box
          sx={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            flex: 1,
          }}
        >
          <ContextualLoader
            loading
            message="Loading notes..."
            showProgress={false}
            inline
          />
        </Box>
      </PageLayout>
    );
  }

  return (
    <PageLayout
      title="Notes"
      variant="grid"
      actions={
        <>
          {/* Search bar */}
          <Box sx={{
            display: "flex",
            alignItems: "center",
            backgroundColor: theme.palette.action.hover,
            borderRadius: 1,
            px: 1,
            mr: 1,
            maxWidth: 200,
          }}>
            <SearchIcon sx={{ fontSize: 18, opacity: 0.5, mr: 0.5 }} />
            <InputBase
              placeholder="Search notes..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              sx={{ fontSize: "0.8rem", py: 0.25 }}
              size="small"
            />
          </Box>

          {/* Cards / Notes toggle */}
          <Tooltip title="Dashboard Cards">
            <IconButton
              onClick={() => navigate("/")}
              size="small"
              sx={{ opacity: 0.5 }}
            >
              <DashboardIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Sticky Notes">
            <IconButton
              size="small"
              sx={{ color: "primary.main" }}
            >
              <StickyNote2 fontSize="small" />
            </IconButton>
          </Tooltip>

          {/* Add note */}
          <Tooltip title="Add Note">
            <IconButton aria-label="Add note" onClick={handleAddNote} size="small" sx={{ ml: 1 }}>
              <Add fontSize="small" />
            </IconButton>
          </Tooltip>

          <Tooltip title="Closed notes">
            <IconButton
              aria-label="Closed notes"
              onClick={() => setClosedDrawerOpen(true)}
              size="small"
            >
              <Badge
                badgeContent={Object.keys(closedNotes).length}
                color="primary"
                invisible={Object.keys(closedNotes).length === 0}
                sx={{ "& .MuiBadge-badge": { fontSize: "0.6rem", height: 16, minWidth: 16 } }}
              >
                <HistoryIcon fontSize="small" />
              </Badge>
            </IconButton>
          </Tooltip>

          {/* Layout mode cycle */}
          <Tooltip
            title={`Layout: ${LAYOUT_MODE_LABELS[layoutMode]} (click to cycle)`}
          >
            <IconButton
              aria-label={`Layout: ${LAYOUT_MODE_LABELS[layoutMode]}`}
              onClick={handleCycleLayoutMode}
              size="small"
              sx={{ opacity: 0.6 }}
            >
              <LayoutModeIcon fontSize="small" />
            </IconButton>
          </Tooltip>

          {/* Save indicator */}
          {saveIndicator && (
            <Fade in>
              <Box sx={{ display: "flex", alignItems: "center", ml: 1, opacity: 0.6 }}>
                {saveIndicator === "saving" && (
                  <Typography variant="caption" sx={{ fontSize: "0.65rem", color: "text.secondary" }}>Saving...</Typography>
                )}
                {saveIndicator === "saved" && (
                  <Tooltip title="All changes saved">
                    <CloudDone sx={{ fontSize: 16, color: "success.main" }} />
                  </Tooltip>
                )}
                {saveIndicator === "error" && (
                  <Tooltip title="Failed to save">
                    <CloudOff sx={{ fontSize: 16, color: "error.main" }} />
                  </Tooltip>
                )}
              </Box>
            </Fade>
          )}
        </>
      }
    >
      <Box
        sx={{
          flex: 1,
          overflow: "auto",
          p: 0.5,
          display: "flex",
          flexDirection: "column",
        }}
        onContextMenu={(e) => {
          // Only show desktop menu if click is on the background (not a note)
          if (!e.target.closest('[data-card-id]')) {
            e.preventDefault();
            setDesktopMenu({ x: e.clientX, y: e.clientY });
          }
        }}
      >
        {layoutError && (
          <MuiAlert
            severity="warning"
            sx={{ mb: 1 }}
            onClose={() => setLayoutError(null)}
          >
            {layoutError}
          </MuiAlert>
        )}

        <Box
          ref={gridContainerRef}
          sx={{
            width: "100%",
            "& .react-grid-item": {
              transition: "transform 0.2s ease-out !important",
              "&.react-grid-placeholder": {
                transition: "all 0.2s ease-out !important",
                opacity: 0.15,
                background: "transparent",
                border: `1px dashed ${theme.palette.primary.main}`,
                borderRadius: "4px",
              },
              "&.react-draggable-dragging": {
                transition: "none !important",
                opacity: 0.9,
                // Outline the note, not the grid cell, so a minimized bar
                // does not drag an expanded-card frame.
                "& > div": {
                  outline: `2px solid ${theme.palette.primary.main}`,
                  borderRadius: "4px",
                },
              },
            },
            // Global handles sit above the card. Keep the corner and the top/right
            // strips off the title bar so Close and the title receive the click.
            "& .react-resizable-handle-n": {
              height: 8,
              top: 0,
              left: 12,
              width: "calc(100% - 24px)",
            },
            "& .react-resizable-handle-e": {
              width: 8,
              top: 44,
              right: 0,
              height: "calc(100% - 44px)",
            },
            "& .react-resizable-handle-ne, & .react-resizable-handle-nw, & .react-resizable-handle-se, & .react-resizable-handle-sw": {
              width: 12,
              height: 12,
            },
          }}
        >
          {(() => {
            // Filter layout to match visible children — prevents RGL from dropping hidden note positions
            const barRows = minimizedBarRows(ROW_HEIGHT_PX, CARD_MARGIN_PX);
            const visibleLayout = layout
              .filter(
                (item) => notes[item.i] && (filteredNoteIds === null || filteredNoteIds.has(item.i)),
              )
              .map((item) =>
                minimizedCards[item.i]
                  ? { ...item, h: barRows, minH: barRows, maxH: barRows }
                  : item,
              );
            const isSearching = filteredNoteIds !== null;
            return (
            <ReactGridLayout
              className="layout"
              layout={visibleLayout}
              style={{ transition: "all 0.2s ease-out" }}
              cols={COLS_COUNT}
              rowHeight={ROW_HEIGHT_PX}
              width={gridWidth}
              containerPadding={[CONTAINER_PADDING_PX, CONTAINER_PADDING_PX]}
              margin={[CARD_MARGIN_PX, CARD_MARGIN_PX]}
              isDraggable={true}
              isResizable={true}
              compactType={null}
              preventCollision={false}
              useCSSTransforms={false}
              allowOverlap={true}
              draggableHandle=".note-header"
              draggableCancel="button, input, textarea, select, option, .non-draggable, .note-content"
              onDragStart={isSearching ? undefined : () => {
                draggingRef.current = true;
                const live = { ...minimizedCardsRef.current };
                // A toggle on this same press may already have stored the
                // pre-flip flag. That bit wins over the live map.
                dragMinRef.current = dragMinRef.current
                  ? { ...live, ...dragMinRef.current }
                  : live;
              }}
              onDragStop={isSearching ? undefined : (next) => commitGridLayout(next, true)}
              onResizeStop={isSearching ? undefined : (next) => commitGridLayout(next, false)}
              resizeHandles={["s", "w", "e", "n", "sw", "nw", "se", "ne"]}
            >
            {visibleLayout
              .sort((a, b) => {
                const aPin = pinnedNotes[a.i] ? 1 : 0;
                const bPin = pinnedNotes[b.i] ? 1 : 0;
                return bPin - aPin;
              })
              .map((layoutItem) => {
                const noteId = layoutItem.i;
                const note = notes[noteId];
                const isMinimized = minimizedCards[noteId] || false;
                const noteColor = noteColors[noteId] || NOTE_COLORS[0];
                const textColor = getContrastColor(noteColor);

                return (
                  <div
                    key={noteId}
                    data-card-id={noteId}
                    style={{
                      transition:
                        "transform 0.2s ease-out, box-shadow 0.2s ease-out",
                      height: isMinimized ? "auto" : "100%",
                      maxHeight: "100%",
                      overflow: "hidden",
                    }}
                    onMouseDown={() => handleCardClick(noteId)}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      const contentEl = e.target.closest?.(".note-content");
                      const inContent = Boolean(contentEl);
                      editRangeRef.current = null;
                      if (inContent) {
                        const sel = window.getSelection();
                        if (sel && sel.rangeCount > 0 && contentEl.contains(sel.anchorNode)) {
                          editRangeRef.current = sel.getRangeAt(0).cloneRange();
                        }
                      }
                      setContextMenu({ noteId, x: e.clientX, y: e.clientY, inContent });
                    }}
                  >
                    {isCollapsed ? (
                      <Paper
                        elevation={1}
                        className="note-header"
                        sx={{
                          display: "flex",
                          alignItems: "center",
                          height: "100%",
                          px: 2,
                          cursor: "grab",
                          userSelect: "none",
                          borderRadius: "8px",
                          backgroundColor: noteColor,
                          backdropFilter: "blur(12px)",
                          border: "1px solid rgba(255,255,255,0.06)",
                          transition:
                            "background-color 0.15s ease, box-shadow 0.15s ease",
                          "&:hover": { boxShadow: theme.shadows[4], backgroundColor: "rgba(255,255,255,0.04)" },
                          "&:active": { cursor: "grabbing" },
                        }}
                        onClick={() => {
                          setLayoutMode("normal");
                          saveState(normalLayoutRef.current || layout, noteColors, minimizedCards, "normal");
                        }}
                      >
                        {pinnedNotes[noteId] && (
                          <PushPin sx={{ fontSize: 12, color: textColor, opacity: 0.5, mr: 0.5 }} />
                        )}
                        <Typography
                          variant="body2"
                          sx={{
                            fontWeight: 500,
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            color: textColor,
                            pointerEvents: "none",
                            flex: 1,
                          }}
                        >
                          {note.title || (note.content
                            ? note.content.replace(/<[^>]*>/g, "").substring(0, 40) || "Empty note"
                            : "Empty note")}
                        </Typography>
                        <IconButton
                          aria-label="Close note"
                          size="small"
                          className="non-draggable"
                          onMouseDown={(e) => e.stopPropagation()}
                          onClick={(e) => {
                            e.stopPropagation();
                            handleCloseNote(noteId);
                          }}
                          sx={{ ml: 0.5, width: 20, height: 20, p: 0, color: textColor, opacity: 0.5 }}
                        >
                          <Close sx={{ fontSize: 16 }} />
                        </IconButton>
                      </Paper>
                    ) : (
                      <StickyNote
                        noteId={noteId}
                        title={note.title || ""}
                        content={note.content}
                        color={noteColor}
                        textColor={textColor}
                        isMinimized={isMinimized}
                        isPinned={!!pinnedNotes[noteId]}
                        onToggleMinimize={() =>
                          handleToggleMinimize(noteId)
                        }
                        onClose={() => handleCloseNote(noteId)}
                        onTitleChange={(nextTitle) =>
                          handleNoteTitleChange(noteId, nextTitle)
                        }
                        onColorChange={(color) =>
                          handleNoteColorChange(noteId, color)
                        }
                        onContentChange={(content) =>
                          handleNoteContentChange(noteId, content)
                        }
                        onDeleteRequest={() => setDeleteConfirm(noteId)}
                        onFormat={handleFormat}
                        onInsertLink={handleInsertLink}
                        theme={theme}
                        noteRef={(el) => {
                          noteRefs.current[noteId] = el;
                        }}
                      />
                    )}
                  </div>
                );
              })}
          </ReactGridLayout>
            );
          })()}
        </Box>
      </Box>

      {/* ── Right-click context menu ───────────────────────────────── */}
      <Menu
        open={Boolean(contextMenu)}
        onClose={() => setContextMenu(null)}
        anchorReference="anchorPosition"
        anchorPosition={contextMenu ? { top: contextMenu.y, left: contextMenu.x } : undefined}
        slotProps={{ paper: { sx: { minWidth: 180, borderRadius: "6px" } } }}
      >
        {contextMenu?.inContent && (
          <>
            <MenuItem onClick={() => contextMenu && handleCopyNote(contextMenu.noteId)}>
              <ListItemIcon><ContentCopy fontSize="small" /></ListItemIcon>
              <ListItemText>Copy</ListItemText>
            </MenuItem>
            <MenuItem onClick={() => contextMenu && handleSelectAllNote(contextMenu.noteId)}>
              <ListItemIcon><SelectAll fontSize="small" /></ListItemIcon>
              <ListItemText>Select all</ListItemText>
            </MenuItem>
            <MenuItem onClick={() => contextMenu && handlePasteNote(contextMenu.noteId)}>
              <ListItemIcon><ContentPaste fontSize="small" /></ListItemIcon>
              <ListItemText>Paste</ListItemText>
            </MenuItem>
            <Divider />
          </>
        )}
        {/* Color swatches */}
        <MenuItem disableRipple disableGutters sx={{ px: 1.5, py: 0.6 }}>
          <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}>
            {NOTE_COLORS.map((c) => (
              <Box
                key={c}
                onClick={() => {
                  if (contextMenu) handleNoteColorChange(contextMenu.noteId, c);
                  setContextMenu(null);
                }}
                sx={{
                  width: 20,
                  height: 20,
                  borderRadius: "50%",
                  backgroundColor: c,
                  cursor: "pointer",
                  border: noteColors[contextMenu?.noteId] === c
                    ? `2px solid ${theme.palette.primary.main}`
                    : "1px solid rgba(255,255,255,0.15)",
                  "&:hover": { transform: "scale(1.2)" },
                  transition: "transform 0.1s ease",
                }}
              />
            ))}
          </Box>
        </MenuItem>
        <Divider />
        <MenuItem onClick={() => {
          if (contextMenu) handleDuplicateNote(contextMenu.noteId);
          setContextMenu(null);
        }}>
          <ListItemIcon><ContentCopy fontSize="small" /></ListItemIcon>
          <ListItemText>Duplicate</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => {
          if (contextMenu) {
            const note = notes[contextMenu.noteId];
            setRenameTarget({ noteId: contextMenu.noteId, title: note?.title || "" });
          }
          setContextMenu(null);
        }}>
          <ListItemIcon><EditIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Rename</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => {
          if (contextMenu) handleTogglePin(contextMenu.noteId);
          setContextMenu(null);
        }}>
          <ListItemIcon>
            {pinnedNotes[contextMenu?.noteId]
              ? <PushPin fontSize="small" />
              : <PushPinOutlined fontSize="small" />}
          </ListItemIcon>
          <ListItemText>{pinnedNotes[contextMenu?.noteId] ? "Unpin" : "Pin to Top"}</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem
          onClick={() => {
            if (contextMenu) setDeleteConfirm({ noteId: contextMenu.noteId, source: "open" });
            setContextMenu(null);
          }}
          sx={{ color: "error.main" }}
        >
          <ListItemIcon><Delete fontSize="small" color="error" /></ListItemIcon>
          <ListItemText>Delete</ListItemText>
        </MenuItem>
      </Menu>

      {/* ── Delete confirmation dialog ─────────────────────────────── */}
      <Dialog open={Boolean(deleteConfirm)} onClose={() => setDeleteConfirm(null)} maxWidth="xs">
        <DialogTitle sx={{ fontSize: "0.9rem" }}>
          {`Delete "${deleteNoteTitle}"? This cannot be undone.`}
        </DialogTitle>
        <DialogActions>
          <Button onClick={() => setDeleteConfirm(null)} size="small">Cancel</Button>
          <Button
            onClick={() => {
              if (deleteConfirm) handleDeleteNote(deleteConfirm.noteId, deleteConfirm.source);
              setDeleteConfirm(null);
            }}
            color="error"
            variant="contained"
            size="small"
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>

      {/* ── Desktop (background) right-click menu ──────────────────── */}
      <Menu
        open={Boolean(desktopMenu)}
        onClose={() => setDesktopMenu(null)}
        anchorReference="anchorPosition"
        anchorPosition={desktopMenu ? { top: desktopMenu.y, left: desktopMenu.x } : undefined}
        slotProps={{ paper: { sx: { minWidth: 160, borderRadius: "6px" } } }}
      >
        <MenuItem onClick={() => { handleAddNote(); setDesktopMenu(null); }}>
          <ListItemIcon><Add fontSize="small" /></ListItemIcon>
          <ListItemText>New Note</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem onClick={() => { handleCycleLayoutMode(); setDesktopMenu(null); }}>
          <ListItemIcon><LayoutModeIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Cycle Layout</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => { navigate("/"); setDesktopMenu(null); }}>
          <ListItemIcon><DashboardIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Go to Dashboard</ListItemText>
        </MenuItem>
      </Menu>

      {/* ── Rename dialog ──────────────────────────────────────────── */}
      <Dialog
        open={Boolean(renameTarget)}
        onClose={() => setRenameTarget(null)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle sx={{ fontSize: "0.9rem" }}>Rename Note</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            size="small"
            value={renameTarget?.title || ""}
            onChange={(e) => setRenameTarget(prev => prev ? { ...prev, title: e.target.value } : null)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                if (renameTarget) {
                  handleNoteTitleChange(renameTarget.noteId, renameTarget.title);
                  setRenameTarget(null);
                }
              }
            }}
            placeholder="Note title"
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRenameTarget(null)} size="small">Cancel</Button>
          <Button
            onClick={() => {
              if (renameTarget) {
                handleNoteTitleChange(renameTarget.noteId, renameTarget.title);
                setRenameTarget(null);
              }
            }}
            variant="contained"
            size="small"
          >
            Save
          </Button>
        </DialogActions>
      </Dialog>

      <ClosedNotesDrawer
        open={closedDrawerOpen}
        onClose={() => setClosedDrawerOpen(false)}
        closedNotes={closedNotes}
        onReopen={handleReopenNote}
        onDeleteRequest={(noteId) => setDeleteConfirm({ noteId, source: "closed" })}
      />
    </PageLayout>
  );
};

export default StickyNotesPage;
