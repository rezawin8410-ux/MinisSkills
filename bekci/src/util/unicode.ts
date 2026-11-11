/**
 * Characters that render as nothing (or as a space) and are used to split keywords so that
 * "ignore previous instructions" no longer matches a filter. Excludes bidi controls, tag
 * characters and variation selectors, which BK001 handles separately.
 */
export const INVISIBLE_CHARS =
  '\u00AD\u034F\u061C\u115F\u1160\u17B4\u17B5\u180B-\u180F\u200B-\u200F\u2060-\u2064\u206A-\u206F\u3164\uFEFF\uFFA0\uFFF9-\uFFFB\u{1D173}-\u{1D17A}';

const STRIP = new RegExp(
  `[${INVISIBLE_CHARS}\\u202A-\\u202E\\u2066-\\u2069\\u{E0000}-\\u{E007F}\\u{FE00}-\\u{FE0F}\\u{E0100}-\\u{E01EF}]`,
  'u',
);

/**
 * Latin look-alikes from Cyrillic and Greek (the ones that matter for English keywords), plus
 * typographic quotes and spaces. Written as code points so no tool can turn them back into
 * literal look-alike characters in this file.
 */
const CONFUSABLES = new Map<string, string>(
  (
    [
      [0x0430, 'a'],
      [0x0432, 'b'],
      [0x0435, 'e'],
      [0x0451, 'e'],
      [0x043a, 'k'],
      [0x043c, 'm'],
      [0x043d, 'h'],
      [0x043e, 'o'],
      [0x0440, 'p'],
      [0x0441, 'c'],
      [0x0442, 't'],
      [0x0443, 'y'],
      [0x0445, 'x'],
      [0x0456, 'i'],
      [0x0457, 'i'],
      [0x0458, 'j'],
      [0x0455, 's'],
      [0x0501, 'd'],
      [0x051b, 'q'],
      [0x051d, 'w'],
      [0x04bb, 'h'],
      [0x04cf, 'l'],
      [0x0261, 'g'],
      [0x0578, 'n'],
      [0x057d, 'u'],
      [0x0410, 'A'],
      [0x0412, 'B'],
      [0x0415, 'E'],
      [0x041a, 'K'],
      [0x041c, 'M'],
      [0x041d, 'H'],
      [0x041e, 'O'],
      [0x0420, 'P'],
      [0x0421, 'C'],
      [0x0422, 'T'],
      [0x0425, 'X'],
      [0x0423, 'Y'],
      [0x0406, 'I'],
      [0x0408, 'J'],
      [0x0405, 'S'],
      [0x03b1, 'a'],
      [0x03bf, 'o'],
      [0x03c1, 'p'],
      [0x03b9, 'i'],
      [0x03bd, 'v'],
      [0x03ba, 'k'],
      [0x03c4, 't'],
      [0x03c5, 'u'],
      [0x03c7, 'x'],
      [0x0391, 'A'],
      [0x0392, 'B'],
      [0x0395, 'E'],
      [0x0396, 'Z'],
      [0x0397, 'H'],
      [0x0399, 'I'],
      [0x039a, 'K'],
      [0x039c, 'M'],
      [0x039d, 'N'],
      [0x039f, 'O'],
      [0x03a1, 'P'],
      [0x03a4, 'T'],
      [0x03a5, 'Y'],
      [0x03a7, 'X'],
      [0x2018, "'"],
      [0x2019, "'"],
      [0x201c, '"'],
      [0x201d, '"'],
      [0x00a0, ' '],
      [0x2002, ' '],
      [0x2003, ' '],
      [0x2009, ' '],
    ] as Array<[number, string]>
  ).map(([cp, ascii]) => [String.fromCodePoint(cp), ascii]),
);

export interface Normalized {
  text: string;
  /** map[i] = offset in the original text of normalized character i. */
  map: number[];
}

/**
 * Normalises text for keyword matching: drops invisible characters, folds Cyrillic/Greek
 * look-alikes and NFKC-compatible forms (fullwidth letters, ligatures) to ASCII. Keeps an
 * offset map so findings still point at the original text.
 */
export function normalizeForMatching(text: string): Normalized {
  // Fast path: plain ASCII needs no work.
  if (!/[^\x00-\x7f]/.test(text)) return { text, map: [] };
  let out = '';
  const map: number[] = [];
  let i = 0;
  for (const ch of text) {
    const len = ch.length;
    if (ch.charCodeAt(0) < 0x80) {
      out += ch;
      map.push(i);
    } else if (!STRIP.test(ch)) {
      const folded = CONFUSABLES.get(ch) ?? ch.normalize('NFKC');
      for (const unit of folded) {
        out += unit;
        for (let k = 0; k < unit.length; k++) map.push(i);
      }
    }
    i += len;
  }
  return { text: out, map };
}

/** Maps a normalized offset back to the original text. */
export function originalOffset(n: Normalized, index: number): number {
  return n.map.length ? (n.map[index] ?? n.map[n.map.length - 1] ?? 0) : index;
}
