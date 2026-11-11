import type { ScanContext, SourceFile } from '../types.js';
import { MARKDOWN_KINDS } from '../types.js';
import { excerptAround, positionAt } from '../util/text.js';

/**
 * A piece of text to pattern-scan. Either a whole file, or a string extracted from a
 * structured config (an MCP server command line, a hook command) whose findings are
 * pinned to a fixed line.
 */
export interface TextUnit {
  file: SourceFile;
  text: string;
  fixedLine?: number;
  label?: string;
  /** Logical group for cross-file correlation (skill directory, server, hook). */
  group: string;
}

export interface Hit {
  line: number;
  column: number;
  index: number;
  match: RegExpExecArray;
  evidence: string;
}

/** Text units whose content can execute or be executed: everything except binaries. */
export function executableUnits(ctx: ScanContext): TextUnit[] {
  const units: TextUnit[] = [];
  for (const file of ctx.files) {
    if (file.kind === 'binary' || file.kind === 'config' || !file.rawScan) continue;
    units.push({ file, text: file.content, group: file.path });
  }
  for (const s of ctx.servers) {
    // One value per line, so a pattern never runs from the command line into an env value.
    const text = [s.commandLine, ...Object.values(s.env), s.url ?? ''].join('\n');
    units.push({ file: s.file, text, fixedLine: s.line, label: `MCP server "${s.name}"`, group: `${s.file.path}#mcp:${s.name}` });
  }
  for (const h of ctx.hooks) {
    units.push({
      file: h.file,
      text: h.command,
      fixedLine: h.line,
      label: `${h.event} hook`,
      group: `${h.file.path}#hook:${h.line}`,
    });
  }
  return units;
}

/** Markdown-like files read by the model as instructions. */
export function promptFiles(ctx: ScanContext): SourceFile[] {
  return ctx.files.filter((f) => MARKDOWN_KINDS.has(f.kind) && f.rawScan);
}

/** Runs a global regex over a unit and yields hits with line/column resolved. */
export function* hits(unit: TextUnit, re: RegExp): Generator<Hit> {
  const flags = re.flags.includes('g') ? re.flags : re.flags + 'g';
  const g = new RegExp(re.source, flags);
  let m: RegExpExecArray | null;
  while ((m = g.exec(unit.text)) !== null) {
    if (m[0].length === 0) {
      g.lastIndex++;
      continue;
    }
    // Comments in bundled scripts are not executed; they often *describe* attacks.
    if (unit.file.kind === 'script' && !unit.label && isCommentAt(unit.file, unit.text, m.index)) continue;
    const pos = unit.fixedLine ? { line: unit.fixedLine, column: 1 } : positionAt(unit.text, m.index);
    yield {
      line: pos.line,
      column: pos.column,
      index: m.index,
      match: m,
      evidence: excerptAround(unit.text, m.index, m[0].length),
    };
  }
}

/** Prefixes a message with the unit label when the text came from a config entry. */
export function where(unit: TextUnit, message: string): string {
  return unit.label ? `${unit.label}: ${message}` : message;
}

let fenceText: string | undefined;
let fenceOffsets: number[] = [];

/** True when `index` is inside a fenced code block. Fence positions are cached per text. */
export function isInsideFencedCode(text: string, index: number): boolean {
  if (text !== fenceText) {
    fenceOffsets = [...text.matchAll(/^[ \t]*(```|~~~)/gm)].map((m) => m.index!);
    fenceText = text;
  }
  let count = 0;
  for (const off of fenceOffsets) {
    if (off >= index) break;
    count++;
  }
  return count % 2 === 1;
}

/** Line-comment markers per script language. Only these count: a `;` or `--` prefix is code in most languages. */
const LINE_COMMENT: Array<[RegExp, RegExp]> = [
  [/\.(sh|bash|zsh|fish|py|rb|pl|ps1|psm1|r|ya?ml|toml)$|^[^.]*$/i, /^#(?!!)/],
  [/\.(js|mjs|cjs|ts|mts|cts|go|rs|java|kt|swift|c|cc|cpp|h|cs|scala)$/i, /^\/\//],
  [/\.php$/i, /^(\/\/|#(?!\[))/],
  [/\.(lua|sql|hs)$/i, /^--/],
  [/\.applescript$/i, /^(--|#)/],
  [/\.(bat|cmd)$/i, /^(rem\s|::)/i],
];

/**
 * True when `index` sits in a whole-line comment of the script's language. Deliberately
 * conservative: trailing and block comments are treated as code, because an attacker can make
 * code look like a comment far more easily than the other way round.
 */
export function isCommentAt(file: { path: string }, text: string, index: number): boolean {
  const lineStart = text.lastIndexOf('\n', index - 1) + 1;
  const trimmed = text.slice(lineStart, index).trimStart();
  const base = file.path.split(/[\\/]/).pop() ?? '';
  return LINE_COMMENT.some(([ext, marker]) => ext.test(base) && marker.test(trimmed));
}
