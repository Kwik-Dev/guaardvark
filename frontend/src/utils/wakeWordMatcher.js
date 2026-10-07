/**
 * Wake phrase detection for hands-free voice ("Hey <system name>").
 *
 * Works on Whisper transcripts, so it tolerates the ways Whisper spells an
 * unusual name ("guard vark", "guardvark") with a Levenshtein comparison over
 * whole words. The name is whatever the install is branded as. Names of five
 * letters or fewer need a greeting in front ("hey", "ok", ...) and a closer
 * match, because a short bare word turns up in ordinary speech.
 */

const PUNCTUATION = /[,.:;!?'"()[\]{}]/g;
const GREETINGS = ["hey", "ok", "okay", "hi", "hello"];
const SHORT_NAME_LENGTH = 5;
const LONG_NAME_SIMILARITY = 0.7;
const SHORT_NAME_SIMILARITY = 0.8;
// A wake phrase is said near the start; this bounds the work on a long transcript.
const MAX_WORDS_SCANNED = 60;

function levenshteinDistance(a, b) {
  const m = a.length;
  const n = b.length;
  let prev = new Array(n + 1);
  let curr = new Array(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) {
    curr[0] = i;
    for (let j = 1; j <= n; j++) {
      curr[j] =
        a[i - 1] === b[j - 1]
          ? prev[j - 1]
          : 1 + Math.min(prev[j], curr[j - 1], prev[j - 1]);
    }
    [prev, curr] = [curr, prev];
  }
  return prev[n];
}

const normalize = (text) =>
  String(text || "")
    .toLowerCase()
    .replace(PUNCTUATION, "")
    .replace(/\s+/g, " ")
    .trim();

/** Spellings Whisper is known to produce for a name, plus generic splits. */
function nameVariants(name) {
  const variants = new Set([name]);
  if (name === "guaardvark") {
    [
      "guard vark", "guardvark", "guad vark", "guaard vark", "guard bark",
      "guard dark", "guar dark", "guadvark", "gard vark", "godvark", "god vark",
    ].forEach((v) => variants.add(v));
  }
  const noDoubles = name.replace(/(.)\1/g, "$1");
  if (noDoubles !== name) variants.add(noDoubles);
  if (!name.includes(" ") && name.length > 5) {
    const mid = Math.floor(name.length / 2);
    variants.add(`${name.slice(0, mid)} ${name.slice(mid)}`);
  }
  const noSpaces = name.replace(/\s+/g, "");
  if (noSpaces !== name) variants.add(noSpaces);
  return Array.from(variants);
}

/**
 * Look for the wake phrase in a transcript.
 *
 * @param {string} transcription Whisper output, punctuation and case as given.
 * @param {string} systemName The install's name ("Guaardvark" unless branded).
 * @returns {{detected: boolean, remainder: string, matchedPhrase?: string}}
 *   `remainder` is what was said after the wake phrase, in the transcript's own
 *   spelling, so it can be sent as the message; the whole transcript when not detected.
 */
export function checkForWakeWord(transcription, systemName) {
  const original = String(transcription || "").trim();
  const name = normalize(systemName);
  if (!original || !name) return { detected: false, remainder: original };

  const originalTokens = original.split(/\s+/);
  // Each normalized word remembers which original token it came from.
  const words = [];
  originalTokens.slice(0, MAX_WORDS_SCANNED).forEach((token, index) => {
    const word = normalize(token);
    if (word) words.push({ word, index });
  });
  if (words.length === 0) return { detected: false, remainder: original };

  const isShort = name.replace(/\s+/g, "").length <= SHORT_NAME_LENGTH;
  const minSimilarity = isShort ? SHORT_NAME_SIMILARITY : LONG_NAME_SIMILARITY;
  const variants = nameVariants(name);
  const phrases = [];
  for (const variant of variants) {
    for (const greeting of GREETINGS) phrases.push(`${greeting} ${variant}`);
    if (!isShort) phrases.push(variant);
  }
  const maxPhraseWords = Math.max(...phrases.map((p) => p.split(" ").length));

  let best = null;
  for (let start = 0; start < words.length; start++) {
    let span = "";
    for (let end = start; end < words.length && end - start < maxPhraseWords + 1; end++) {
      span = span ? `${span} ${words[end].word}` : words[end].word;
      for (const phrase of phrases) {
        if (Math.abs(span.length - phrase.length) > 3) continue;
        const distance = span === phrase ? 0 : levenshteinDistance(span, phrase);
        const similarity = 1 - distance / Math.max(span.length, phrase.length);
        if (similarity < minSimilarity) continue;
        const better =
          !best ||
          similarity > best.similarity ||
          (similarity === best.similarity && start < best.start) ||
          (similarity === best.similarity && start === best.start && phrase.length > best.phrase.length);
        if (better) best = { similarity, start, end, phrase };
      }
    }
  }

  if (!best) return { detected: false, remainder: original };
  const afterIndex = words[best.end].index + 1;
  const remainder = originalTokens
    .slice(afterIndex)
    .join(" ")
    .replace(/^[\s,.:;!?-]+/, "")
    .trim();
  return { detected: true, remainder, matchedPhrase: best.phrase };
}

export default checkForWakeWord;
