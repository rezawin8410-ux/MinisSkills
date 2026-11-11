export interface Frontmatter {
  fields: Record<string, string | string[]>;
  /** 1-based line of each key. */
  lines: Record<string, number>;
  /** Number of lines taken by the frontmatter block, including both fences. */
  length: number;
}

/**
 * Parses the YAML frontmatter used by SKILL.md, sub-agent and command files. Supports the
 * subset those files use: scalars (plain/quoted), block lists, flow lists and folded or
 * literal block scalars. Unknown constructs are kept as raw strings.
 */
export function parseFrontmatter(content: string): Frontmatter | undefined {
  const lines = content.replace(/^\uFEFF/, '').split(/\r?\n/);
  if (lines[0]?.trim() !== '---') return undefined;
  const end = lines.findIndex((l, idx) => idx > 0 && (l.trim() === '---' || l.trim() === '...'));
  if (end === -1) return undefined;

  const fields: Record<string, string | string[]> = Object.create(null);
  const keyLines: Record<string, number> = Object.create(null);
  // A uniformly indented root mapping is still valid YAML.
  const rootIndent = Math.min(
    ...lines
      .slice(1, end)
      .filter((l) => /^\s*["']?[A-Za-z0-9_-]+["']?\s*:/.test(l))
      .map((l) => /^\s*/.exec(l)![0].length),
    Infinity,
  );
  let i = 1;
  while (i < end) {
    const line = lines[i]!;
    const indent = /^\s*/.exec(line)![0].length;
    const m = indent === rootIndent ? /^\s*["']?([A-Za-z0-9_-]+)["']?\s*:(?:\s+(.*)|$)/.exec(line) : null;
    if (!m) {
      i++;
      continue;
    }
    const key = m[1]!;
    keyLines[key] = i + 1;
    let rest = (m[2] ?? '').trim();
    // Flow sequences may span several lines: `[Read,\n  Bash]`.
    if (rest.startsWith('[') && !rest.includes(']')) {
      while (i + 1 < end && !rest.includes(']')) rest += ' ' + lines[++i]!.trim();
    }
    i++;

    if (rest === '' || rest === '|' || rest === '>' || /^[|>][+-]?$/.test(rest)) {
      const block: string[] = [];
      while (
        i < end &&
        (/^\s*$/.test(lines[i]!) || /^\s*/.exec(lines[i]!)![0].length > rootIndent || /^\s*-\s/.test(lines[i]!))
      ) {
        block.push(lines[i]!);
        i++;
      }
      const items = block.filter((l) => /^\s*-\s+/.test(l));
      if (rest === '' && items.length > 0 && items.length === block.filter((l) => l.trim()).length) {
        fields[key] = items.map((l) => unquote(l.replace(/^\s*-\s+/, '').trim()));
      } else {
        const text = block.map((l) => l.trim());
        fields[key] = rest.startsWith('>') ? text.join(' ').trim() : text.join('\n').trim();
      }
      continue;
    }
    if (rest.startsWith('[') && rest.endsWith(']')) {
      fields[key] = rest
        .slice(1, -1)
        .split(',')
        .map((s) => unquote(s.trim()))
        .filter(Boolean);
      continue;
    }
    fields[key] = unquote(stripComment(rest));
  }
  return { fields, lines: keyLines, length: end + 1 };
}

function stripComment(s: string): string {
  if (s.startsWith('"') || s.startsWith("'")) return s;
  const idx = s.search(/\s#/);
  return idx === -1 ? s : s.slice(0, idx).trim();
}

function unquote(s: string): string {
  if (s.length >= 2 && ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'")))) {
    return s.slice(1, -1);
  }
  return s;
}

/** Splits a tool list that may be a YAML list or a comma/space separated string. */
export function toolList(value: string | string[] | undefined): string[] {
  if (value === undefined) return [];
  if (Array.isArray(value)) return value;
  const out: string[] = [];
  let depth = 0;
  let cur = '';
  for (const ch of value) {
    if (ch === '(') depth++;
    if (ch === ')') depth = Math.max(0, depth - 1);
    if (depth === 0 && (ch === ',' || ch === ' ')) {
      if (cur.trim()) out.push(cur.trim());
      cur = '';
      continue;
    }
    cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}
