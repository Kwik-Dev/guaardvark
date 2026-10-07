// frontend/src/components/dashboard/dashboardCardVisibility.js
// Hidden dashboard cards. `hiddenCards` is a map of card id -> true, saved with
// the rest of the dashboard state. A hidden card keeps its layout item so it
// comes back where it was.

/** Ids of the hidden cards, in a stable order. */
export function hiddenCardIds(hiddenCards) {
  return Object.keys(hiddenCards || {}).filter((id) => hiddenCards[id]);
}

/** The layout without hidden cards: what the grid renders. */
export function visibleLayout(layout, hiddenCards) {
  if (!hiddenCardIds(hiddenCards).length) return layout;
  return layout.filter((item) => !hiddenCards[item.i]);
}

/**
 * The grid only reports the cards it renders, so a layout it hands back after a
 * drag or resize has lost the hidden cards; put their previous items back.
 */
export function withHiddenItems(reported, previous, hiddenCards) {
  const seen = new Set(reported.map((item) => item.i));
  const kept = previous.filter((item) => hiddenCards?.[item.i] && !seen.has(item.i));
  return kept.length ? [...reported, ...kept] : reported;
}

/** hiddenCards with one card shown again, or every card when cardId is omitted. */
export function showCard(hiddenCards, cardId) {
  if (cardId === undefined) return {};
  const next = { ...(hiddenCards || {}) };
  delete next[cardId];
  return next;
}

/**
 * A card hidden while a layout preset was applied has no item in that preset;
 * give it its default size below everything else.
 */
export function ensureLayoutItem(layout, cardId, fallback) {
  if (!fallback || layout.some((item) => item.i === cardId)) return layout;
  const bottom = layout.reduce((max, item) => Math.max(max, (item.y || 0) + (item.h || 0)), 0);
  return [...layout, { ...fallback, i: cardId, x: 0, y: bottom }];
}
