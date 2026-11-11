export interface Position {
  line: number;
  column: number;
}

let cachedText: string | undefined;
let cachedStarts: number[] = [];

/** Offsets at which each line starts. Cached for the most recent text (rules scan one file at a time). */
export function lineStarts(text: string): number[] {
  if (text === cachedText) return cachedStarts;
  const starts = [0];
  for (let i = text.indexOf('\n'); i !== -1; i = text.indexOf('\n', i + 1)) starts.push(i + 1);
  cachedText = text;
  cachedStarts = starts;
  return starts;
}

/** Converts a string offset into a 1-based line/column in O(log n). */
export function positionAt(text: string, offset: number): Position {
  const starts = lineStarts(text);
  let lo = 0;
  let hi = starts.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (starts[mid]! <= offset) lo = mid;
    else hi = mid - 1;
  }
  return { line: lo + 1, column: offset - starts[lo]! + 1 };
}

/** Removes terminal control sequences and bidi controls from text taken from scanned files. */
export function sanitize(s: string): string {
  return s
    .replace(/[\r\n\t]+/g, ' ')
    .replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f]/g, (ch) => `\\x${ch.charCodeAt(0).toString(16).padStart(2, '0')}`)
    .replace(
      /[\u202A-\u202E\u2066-\u2069\u200B-\u200F\u2060\uFEFF]/g,
      (ch) => `<U+${ch.charCodeAt(0).toString(16).toUpperCase()}>`,
    )
    .replace(/[\u{E0000}-\u{E007F}]/gu, '');
}

/** Line (1-based) of the first occurrence of any needle, or `fallback`. */
export function lineOf(text: string, needles: string[], fallback = 1, from = 0): number {
  for (const needle of needles) {
    if (!needle) continue;
    const idx = text.indexOf(needle, from);
    if (idx !== -1) return positionAt(text, idx).line;
  }
  return fallback;
}

/** The text of a 1-based line. */
export function lineText(text: string, line: number): string {
  const starts = lineStarts(text);
  if (line < 1 || line > starts.length) return '';
  const start = starts[line - 1]!;
  const end = text.indexOf('\n', start);
  return text.slice(start, end === -1 ? text.length : end).replace(/\r$/, '');
}

/** A compact, single-line excerpt centred on a match. */
export function excerpt(s: string, max = 100): string {
  const flat = s.replace(/\s+/g, ' ').trim();
  return flat.length > max ? flat.slice(0, max - 1) + '\u2026' : flat;
}

/** Excerpt of the line around `index`, keeping the match visible. */
export function excerptAround(text: string, index: number, length: number, max = 100): string {
  const lineStart = text.lastIndexOf('\n', index - 1) + 1;
  const nl = text.indexOf('\n', index);
  const lineEnd = nl === -1 ? text.length : nl;
  const line = text.slice(lineStart, lineEnd);
  const rel = index - lineStart;
  if (line.length <= max) return excerpt(line, max);
  const start = Math.max(0, Math.min(rel - 20, line.length - max));
  const slice = line.slice(start, start + max);
  void length;
  return (start > 0 ? '\u2026' : '') + excerpt(slice, max) + (start + max < line.length ? '\u2026' : '');
}

/** Masks a secret, keeping just enough to recognise it. */
export function redact(secret: string): string {
  if (secret.length <= 8) return '*'.repeat(secret.length);
  const keep = Math.min(6, Math.floor(secret.length / 4));
  return `${secret.slice(0, keep)}${'*'.repeat(8)} (${secret.length} chars)`;
}

export function levenshtein(a: string, b: string, max = Infinity): number {
  if (a === b) return 0;
  if (Math.abs(a.length - b.length) > max) return max + 1;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    let rowMin = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      const v = Math.min(prev[j]! + 1, cur[j - 1]! + 1, prev[j - 1]! + cost);
      cur.push(v);
      if (v < rowMin) rowMin = v;
    }
    if (rowMin > max) return max + 1;
    prev = cur;
  }
  return prev[b.length]!;
}

/** Converts a glob (`**`, `*`, `?`) into an anchored RegExp over POSIX paths. */
export function globToRegExp(glob: string): RegExp {
  let g = glob.replace(/\\/g, '/').replace(/^\.\//, '');
  if (!g.includes('/')) g = '**/' + g;
  if (g.endsWith('/')) g += '**';
  let re = '';
  for (let i = 0; i < g.length; i++) {
    const c = g[i]!;
    if (c === '*') {
      if (g[i + 1] === '*') {
        i++;
        if (g[i + 1] === '/') {
          i++;
          re += '(?:.*/)?';
        } else {
          re += '.*';
        }
      } else {
        re += '[^/]*';
      }
    } else if (c === '?') {
      re += '[^/]';
    } else {
      re += c.replace(/[.+^${}()|[\]\\]/g, '\\$&');
    }
  }
  // A pattern matching a directory also matches everything below it.
  return new RegExp(`^${re}(?:/.*)?$`);
}
