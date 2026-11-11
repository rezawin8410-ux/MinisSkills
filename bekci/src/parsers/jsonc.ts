/**
 * Parses JSON with comments and trailing commas (the dialect used by VS Code, Cursor
 * and Gemini CLI config files). String contents are never modified.
 */
export function parseJsonc(text: string): unknown {
  return JSON.parse(stripTrailingCommas(stripComments(text.replace(/^\uFEFF/, ''))));
}

function stripComments(text: string): string {
  let out = '';
  let i = 0;
  const n = text.length;
  while (i < n) {
    const c = text[i];
    if (c === '"') {
      const end = endOfString(text, i);
      out += text.slice(i, end);
      i = end;
      continue;
    }
    if (c === '/' && text[i + 1] === '/') {
      while (i < n && text[i] !== '\n') i++;
      continue;
    }
    if (c === '/' && text[i + 1] === '*') {
      i += 2;
      while (i < n && !(text[i] === '*' && text[i + 1] === '/')) {
        if (text[i] === '\n') out += '\n';
        i++;
      }
      i += 2;
      continue;
    }
    out += c;
    i++;
  }
  return out;
}

function stripTrailingCommas(text: string): string {
  let out = '';
  let i = 0;
  const n = text.length;
  while (i < n) {
    const c = text[i];
    if (c === '"') {
      const end = endOfString(text, i);
      out += text.slice(i, end);
      i = end;
      continue;
    }
    if (c === ',') {
      let j = i + 1;
      while (j < n && /\s/.test(text[j]!)) j++;
      if (text[j] === '}' || text[j] === ']') {
        i++;
        continue;
      }
    }
    out += c;
    i++;
  }
  return out;
}

/** Index just past the closing quote of the string starting at `start`. */
function endOfString(text: string, start: number): number {
  let i = start + 1;
  while (i < text.length) {
    if (text[i] === '\\') {
      i += 2;
      continue;
    }
    if (text[i] === '"') return i + 1;
    i++;
  }
  return text.length;
}
