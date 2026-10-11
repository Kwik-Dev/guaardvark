// frontend/src/utils/videoPreviewPrompts.js
//
// The prompts "Preview enhanced" sends to /batch-video/enhance-preview: the
// same strings Add to queue sends, one per clip, so the preview shows what
// will be sent rather than the first line alone. Mirrors handleGenerate on
// VideoGeneratorPage: Text input sends one prompt per line with Look & Feel
// appended to each; Image and References input send the one description,
// with Look & Feel appended when there is one.

// The preview route takes one prompt per call; the enhancer is a pure
// function, so a batch is previewed line by line up to this many.
export const PREVIEW_LINE_CAP = 20;

/**
 * @param {object} args
 * @param {"text"|"image"|"references"} args.inputMode
 * @param {string} args.promptsText
 * @param {string} [args.lookAndFeel]
 * @param {boolean} [args.storyboardMode]  Text input: the box is one concept
 * @returns {{prompts: string[], total: number, capped: boolean}}
 */
export function promptsToPreview({ inputMode, promptsText, lookAndFeel = "", storyboardMode = false }) {
  const lf = (lookAndFeel || "").trim();
  if (inputMode === "text" && !storyboardMode) {
    const lines = (promptsText || "").split("\n").map((p) => p.trim()).filter(Boolean);
    const all = lf ? lines.map((p) => `${p}, ${lf}`) : lines;
    return { prompts: all.slice(0, PREVIEW_LINE_CAP), total: all.length, capped: all.length > PREVIEW_LINE_CAP };
  }
  const one = (promptsText || "").trim();
  if (!one) return { prompts: [], total: 0, capped: false };
  return { prompts: [lf ? `${one}, ${lf}` : one], total: 1, capped: false };
}

/**
 * The label over the preview box.
 *
 * @param {object} args
 * @param {number} args.shown  prompts previewed
 * @param {number} args.total  prompts the batch will send
 * @param {boolean} [args.verbatim]  Settings > Verbatim prompts is on
 * @param {boolean} [args.enhanceOff]  Enhance prompt is off
 * @param {boolean} [args.storyboard]  the Director will expand this concept
 */
export function previewLabel({ shown, total, verbatim = false, enhanceOff = false, storyboard = false }) {
  const asTyped = verbatim
    ? "as typed (Verbatim prompts is on in Settings)"
    : enhanceOff
      ? "as typed (Enhance prompt is off)"
      : null;
  if (storyboard) {
    return asTyped
      ? `Concept ${asTyped}; the Storyboard step still writes the shots`
      : "Enhanced concept; the Storyboard step writes the shots from it, so the clips get different prompts";
  }
  if (total <= 1) {
    return asTyped ? `Prompt ${asTyped}` : "Enhanced prompt (what will be sent)";
  }
  const count = shown < total ? `first ${shown} of ${total} clips` : `${total} clips, one each`;
  return asTyped ? `Prompts ${asTyped}: ${count}` : `Enhanced prompts (what will be sent): ${count}`;
}

/** One block per clip, numbered when there is more than one. */
export function joinPreviews(previews) {
  if (previews.length <= 1) return previews[0] || "";
  return previews.map((p, i) => `${i + 1}. ${p}`).join("\n\n");
}
