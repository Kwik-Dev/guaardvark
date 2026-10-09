// An opaque surface for a menu that opens over coloured content.
// Some themes give Paper a see-through glass background (Guaardvark's is 3%
// white with a blur), which leaves menu text unreadable over a light note.

import { decomposeColor } from "@mui/material/styles";

function isOpaque(color) {
  try {
    const { type, values } = decomposeColor(color);
    return !type.endsWith("a") || values[3] >= 1;
  } catch {
    return false;
  }
}

/** The theme's paper colour when it is solid, else its page background, else a plain fallback. */
export function opaqueSurface(theme) {
  const { paper, default: page } = theme.palette.background;
  if (isOpaque(paper)) return paper;
  if (isOpaque(page)) return page;
  return theme.palette.mode === "light" ? "#ffffff" : "#121212";
}

/** sx for a Menu's paper slot: the theme's colours, never see-through. */
export const opaqueMenuPaperSx = (theme) => ({
  backgroundColor: opaqueSurface(theme),
  backgroundImage: "none",
  backdropFilter: "none",
});
