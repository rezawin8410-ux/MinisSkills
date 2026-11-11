import type { Rule, Severity } from '../types.js';
import { MARKDOWN_KINDS } from '../types.js';
import { excerpt } from '../util/text.js';
import { executableUnits, hits, promptFiles, type TextUnit, where } from './helpers.js';

const SHELLS = '(?:sudo\\s+(?:-\\S+\\s+)*)?(?:ba|z|da|k|fi|c|tc)?sh';
const INTERPRETERS = '(?:sudo\\s+)?(?:python[0-9.]*|node|perl|ruby|php|deno\\s+run|bun|osascript|pwsh|powershell)';

/** A shell or interpreter, optionally by absolute path or via `env`. */
const RUNNER = `(?:sudo\\s+(?:-\\S+\\s+)*)?(?:(?:/usr)?(?:/local)?/s?bin/)?(?:env\\s+(?:-\\S+\\s+)*)?(?:(?:ba|z|da|k|fi|c|tc)?sh|${INTERPRETERS.replace('(?:sudo\\s+)?', '')})`;

const RCE_PATTERNS: RegExp[] = [
  // curl ... | sh, including `| tee f | sh`, `|& bash`, URLs with `&`, `/bin/bash`, `env bash`
  // The interpreter must read its program from stdin: `| python3 -c '...'`, `| node script.js` or
  // `| python3 -m json.tool` only process the downloaded data.
  new RegExp(
    `\\b(curl|wget|fetch)\\s+[^\\s|;&][^\\n;]{0,300}?\\|&?\\s*${RUNNER}\\b(?![ \\t]+(?:-[A-Za-z]*[cemp]\\b|[^\\s\\-|;&)'"\`]))`,
    'i',
  ),
  // bash <(curl ...), . <(curl ...), source <(curl ...)
  new RegExp(`(?:^|[\\s;&|(])(?:${RUNNER}|source|\\.)\\s+(?:-c\\s+)?["']?\\s*<\\(\\s*(curl|wget)\\b`, 'im'),
  // eval "$(curl ...)", bash -c "$(curl ...)", node -e "$(curl ...)", python3 -c "$(curl ...)"
  new RegExp(`(?:\\beval|${RUNNER}\\s+-[ce])\\s+["']?\\$\\(\\s*(curl|wget)\\b`, 'i'),
  /\b(iex|invoke-expression)\b[^\n]{0,300}\b(iwr|irm|invoke-webrequest|invoke-restmethod|downloadstring|net\.webclient)\b/i,
  /\b(iwr|irm|invoke-webrequest|invoke-restmethod)\b[^\n]{0,300}\|\s*(iex|invoke-expression)\b/i,
  // exec(urlopen(...).read()), eval(requests.get(...).text), eval(await (await fetch(...)).text())
  /\b(exec|eval)\s*\([^\n]{0,120}\b(urlopen|urllib\.request|requests\.get|httpx\.get)\s*\(/i,
  /\b(eval|Function|new\s+Function)\s*\([^\n]{0,120}\bfetch\s*\(/,
];

/**
 * Executable contexts (hooks, servers, scripts) and agent-facing skill/command files get the base
 * severity; project instruction files (CLAUDE.md, AGENTS.md), which mostly document setup for
 * humans, get the lower one.
 */
function unitSeverity(unit: TextUnit, base: Severity, inDocs: Severity): Severity {
  return (unit.file.kind === 'instructions' || unit.file.kind === 'reference') && !unit.label ? inDocs : base;
}

export const remoteCodeExecution: Rule = {
  id: 'BK005',
  name: 'remote-code-execution',
  title: 'Downloads and executes remote code',
  severity: 'high',
  category: 'code-execution',
  description:
    'A command pipes content fetched from the network straight into a shell or interpreter (`curl \u2026 | sh`, ' +
    '`bash <(curl \u2026)`, `iex (iwr \u2026)`). Whatever the server returns at that moment runs with your privileges, ' +
    'and it can change after you reviewed it. Reported as high in hooks, MCP server commands, scripts, skills and ' +
    'commands, and as medium in project instruction files such as CLAUDE.md.',
  remediation:
    'Download the script, review it, pin it by checksum, then run the local copy, or install from a package manager with a pinned version.',
  run(ctx, report) {
    for (const unit of executableUnits(ctx)) {
      const lines = new Set<number>();
      for (const re of RCE_PATTERNS) {
        for (const h of hits(unit, re)) {
          if (lines.has(h.line)) continue;
          // `curl ... | sh` or `curl <URL> | sh` is a sentence about the pattern, not a command.
          if (/^(curl|wget)\s+(\.\.\.|\u2026|<[^>]*>)\s*\\?\|/i.test(h.match[0])) continue;
          lines.add(h.line);
          report({
            file: unit.file,
            line: h.line,
            column: h.column,
            severity: unitSeverity(unit, 'high', 'medium'),
            message: where(unit, `Pipes remote content into an interpreter: \`${excerpt(h.match[0], 80)}\``),
            evidence: h.evidence,
          });
        }
      }
    }
  },
};

const OBFUSCATION_PATTERNS: RegExp[] = [
  new RegExp(`\\bbase64\\s+(-d|--decode|-D)\\b[^\\n]{0,300}\\|\\s*(${SHELLS}|${INTERPRETERS})\\b`, 'i'),
  /\b(echo|printf)\s+['"]?[A-Za-z0-9+/]{40,}={0,2}['"]?\s*\|\s*base64\s+(-d|--decode|-D)/i,
  /\beval\s*\(\s*(atob|Buffer\.from|unescape|decodeURIComponent)\s*\(/i,
  /\b(exec|eval)\s*\(\s*(base64\.b64decode|codecs\.decode|bytes\.fromhex|zlib\.decompress|marshal\.loads|__import__\(['"]base64)/i,
  /\bnew\s+Function\s*\(\s*(atob|Buffer\.from)\s*\(/i,
  /\b(xxd\s+-r\s+-p|openssl\s+(base64|enc)\s+-d)\b[^\n]{0,300}\|\s*(ba|z)?sh\b/i,
  /\\x[0-9a-f]{2}(\\x[0-9a-f]{2}){30,}/i,
];

const BASE64_BLOB = /(?<![A-Za-z0-9+/=]|data:[\w/+.-]{1,40};base64,)[A-Za-z0-9+/]{240,}={0,2}(?![A-Za-z0-9+/])/g;

export const obfuscatedPayload: Rule = {
  id: 'BK006',
  name: 'obfuscated-payload',
  title: 'Obfuscated or encoded payload',
  severity: 'high',
  category: 'code-execution',
  description:
    'Code is decoded at runtime and then executed (`base64 -d | sh`, `eval(atob(...))`, ' +
    '`exec(base64.b64decode(...))`), or a large encoded blob is embedded in an instruction file. Encoding hides ' +
    'what will run from anyone reviewing the file.',
  remediation: 'Decode the payload offline to see what it does. Legitimate tools ship readable source.',
  run(ctx, report) {
    for (const unit of executableUnits(ctx)) {
      const lines = new Set<number>();
      for (const re of OBFUSCATION_PATTERNS) {
        for (const h of hits(unit, re)) {
          if (lines.has(h.line)) continue;
          lines.add(h.line);
          report({
            file: unit.file,
            line: h.line,
            column: h.column,
            message: where(unit, `Decodes and executes hidden code: \`${excerpt(h.match[0], 80)}\``),
            evidence: h.evidence,
          });
        }
      }
    }
    for (const file of promptFiles(ctx)) {
      for (const h of hits({ file, text: file.content, group: file.path }, BASE64_BLOB)) {
        let decoded = '';
        try {
          decoded = Buffer.from(h.match[0], 'base64').toString('utf8');
        } catch {
          /* ignore */
        }
        const printable = decoded.replace(/[^\x20-\x7e\n\t]/g, '').length / Math.max(1, decoded.length);
        report({
          file,
          line: h.line,
          column: h.column,
          severity: 'medium',
          message:
            `Large base64 blob (${h.match[0].length} chars) embedded in an instruction file` +
            (printable > 0.9 ? `; decodes to text: "${excerpt(decoded, 80)}"` : ''),
        });
      }
    }
  },
};

const DESTRUCTIVE: Array<[RegExp, string, Severity, boolean?]> = [
  [
    /\brm\s+(?:--?[^\s-][\w-]*\s+){0,20}?(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)(?:\s+--?[^\s-][\w-]*){0,20}\s+["']?(\/\*?|~\/?\*?|\$\{?HOME\}?\/?\*?)["']?(?=\s|$|[;&|)`"'])/,
    'recursively deletes the root or home directory',
    'high',
  ],
  [/\brm\b[^\n]{0,300}--no-preserve-root/, 'deletes with --no-preserve-root', 'high'],
  [/\bmkfs(\.\w+)?\s+\/dev\//, 'formats a disk', 'high'],
  [/\bdd\s+[^\n]{0,300}\bof=\/dev\/(sd|disk|nvme|hd|mmcblk)/, 'overwrites a raw disk device', 'high'],
  [/:\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:/, 'fork bomb', 'high'],
  [/\bdiskutil\s+(eraseDisk|zeroDisk|secureErase|eraseVolume)\b/, 'erases a disk', 'high'],
  [/\bchmod\s+(-R\s+)?0?777\s+(\/|~|\$HOME)(\s|$)/, 'makes the root or home directory world-writable', 'medium'],
  [
    /\bRemove-Item\b[^\n]{0,300}-Recurse[^\n]{0,300}(C:\\\s|C:\\$|\$env:USERPROFILE|\$HOME|~)/i,
    'recursively deletes the system drive or user profile',
    'high',
  ],
  [/\bgit\s+push\b[^\n]{0,300}\s(--force|-f)(\s|$)/, 'force-pushes, rewriting remote history', 'low'],
  [/\bgit\s+reset\s+--hard\s+origin/, 'discards local work with git reset --hard', 'low'],
  [/\b(DROP\s+(DATABASE|SCHEMA)|TRUNCATE\s+TABLE)\b/, 'drops or truncates database objects', 'medium', true],
];

// Letter boundaries rather than \b, so BLOCKED_COMMANDS and deny_list still match.
const DENY_CONTEXT =
  /(?<![a-z])(block(ed|list)?|deny|denied|deny_?list|forbid(den)?|blacklist|disallow(ed)?|prohibit(ed)?|dangerous|unsafe|never\s+run|do\s+not\s+run)(?![a-z])/i;

function lineAround(text: string, index: number): string {
  const start = text.lastIndexOf('\n', index - 1) + 1;
  const end = text.indexOf('\n', index);
  return text.slice(start, end === -1 ? text.length : end);
}

export const destructiveCommand: Rule = {
  id: 'BK007',
  name: 'destructive-command',
  title: 'Destructive command',
  severity: 'medium',
  category: 'code-execution',
  description:
    'A hook, script, MCP server command or instruction contains a command that destroys data (`rm -rf ~`, ' +
    '`mkfs`, `dd of=/dev/\u2026`, force pushes). An agent that follows the instruction, or a hook that fires on every ' +
    'tool call, can do irreversible damage.',
  remediation: 'Scope destructive commands to an explicit, safe path and require confirmation before running them.',
  run(ctx, report) {
    for (const unit of executableUnits(ctx)) {
      const lines = new Set<number>();
      // Documentation lists destructive commands far more often than it runs them (deny lists,
      // hook examples): keep it visible, but low.
      const docs = MARKDOWN_KINDS.has(unit.file.kind) && !unit.label;
      for (const [re, what, sev, executableOnly] of DESTRUCTIVE) {
        // SQL in documentation is almost always an example, not an instruction.
        if (executableOnly && docs) continue;
        for (const h of hits(unit, re)) {
          if (lines.has(h.line)) continue;
          // Deny lists and guards name the commands they block: BLOCKED_COMMANDS="rm -rf /,mkfs,..."
          if (DENY_CONTEXT.test(lineAround(unit.text, h.index))) continue;
          lines.add(h.line);
          report({
            file: unit.file,
            line: h.line,
            column: h.column,
            severity: docs ? 'low' : sev,
            message: where(unit, `Command ${what}: \`${excerpt(h.match[0], 60)}\``),
            evidence: h.evidence,
          });
        }
      }
    }
  },
};
