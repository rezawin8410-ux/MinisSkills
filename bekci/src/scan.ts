import { createRequire } from 'node:module';
import { discover } from './discover.js';
import { findRule, RULES } from './rules/index.js';
import type { Agent, Finding, Rule, ScanContext, ScanResult, Severity } from './types.js';
import { severityRank } from './types.js';
import { redactSecrets } from './rules/secrets.js';
import { globToRegExp, lineText, sanitize } from './util/text.js';

const pkg = createRequire(import.meta.url)('../package.json') as { version: string };
export const VERSION = pkg.version;

export interface ScanOptions {
  /** Paths to scan as project scope. Defaults to the current directory; pass [] for none. */
  paths?: string[];
  /** Also scan user-level agent configuration in the home directory. Default: true. */
  includeUser?: boolean;
  /** Follow symlinks inside scanned paths. Default: true. */
  followSymlinks?: boolean;
  /** Honour `bekci-ignore` comments in scanned files. Default: true. */
  inlineIgnores?: boolean;
  /** Rule IDs or names to skip. */
  ignoreRules?: string[];
  /** Globs whose findings are dropped. */
  ignorePaths?: string[];
  /** Drop findings below this severity. Default: low. */
  minSeverity?: Severity;
  /** Restrict to these rule IDs. */
  onlyRules?: string[];
  /** Override the home directory (tests). */
  home?: string;
  /** Custom rule set (defaults to all built-in rules). */
  rules?: readonly Rule[];
}

export interface ScanOutput extends ScanResult {
  suppressed: number;
}

const WEIGHTS: Record<Severity, number> = { critical: 40, high: 15, medium: 5, low: 1, info: 0 };

export async function scan(options: ScanOptions = {}): Promise<ScanOutput> {
  const started = Date.now();
  // An explicit empty list means "no project paths" (user-level only).
  const paths = options.paths ?? [process.cwd()];
  const found = await discover({
    paths,
    includeUser: options.includeUser ?? true,
    followSymlinks: options.followSymlinks ?? true,
    home: options.home,
  });

  const errors = [...found.errors];

  const ignored = new Set((options.ignoreRules ?? []).map((r) => findRule(r)?.id ?? r.toUpperCase()));
  const only = options.onlyRules?.length ? new Set(options.onlyRules.map((r) => findRule(r)?.id ?? r.toUpperCase())) : undefined;
  const pathFilters = (options.ignorePaths ?? []).map(globToRegExp);
  // Ignored paths are removed before analysis, so they neither produce findings nor feed
  // cross-file rules (lethal trifecta) nor inflate the stats.
  const kept = (file: { displayPath: string }) => !pathFilters.some((re) => re.test(file.displayPath));
  const ctx: ScanContext = {
    files: found.files.filter(kept),
    servers: found.servers.filter((s) => kept(s.file)),
    hooks: found.hooks.filter((h) => kept(h.file)),
  };
  const minRank = severityRank(options.minSeverity ?? 'low');
  const inlineIgnores = options.inlineIgnores ?? true;

  const findings: Finding[] = [];
  const seen = new Set<string>();
  let suppressed = 0;
  let ruleFailed = false;

  for (const rule of options.rules ?? RULES) {
    if (ignored.has(rule.id) || (only && !only.has(rule.id))) continue;
    try {
      rule.run(ctx, (input) => {
        // Test fixtures of scanners and skill repos contain attacks on purpose. They are not loaded
        // by agents, so they are reported one level lower and labelled.
        const fixture = isFixture(input.file.displayPath);
        const severity = fixture ? lower(input.severity ?? rule.severity) : (input.severity ?? rule.severity);
        if (fixture) input = { ...input, message: `${input.message} (test fixture)` };
        if (severityRank(severity) < minRank) return;
        const file = input.file;
        if (pathFilters.some((re) => re.test(file.displayPath))) return;
        // Inline comments are written by whoever wrote the file. Only honour them in the project's
        // own files (not in skills, plugins or anything else vendored in), and never for criticals.
        if (
          inlineIgnores &&
          severity !== 'critical' &&
          file.scope === 'project' &&
          !file.vendored &&
          input.line &&
          isSuppressed(file.content, input.line, rule.id)
        ) {
          suppressed++;
          return;
        }
        const key = `${rule.id}|${file.path}|${input.line ?? 0}|${input.message}`;
        if (seen.has(key)) return;
        seen.add(key);
        findings.push({
          ruleId: rule.id,
          ruleName: rule.name,
          category: rule.category,
          severity,
          title: rule.title,
          // Everything below may contain attacker-controlled text: never let it carry terminal
          // escapes or a full secret into any report.
          message: redactSecrets(sanitize(input.message)),
          file: sanitize(file.displayPath),
          absPath: file.path,
          agent: file.agent,
          line: input.line,
          column: input.column,
          evidence: input.evidence === undefined ? undefined : redactSecrets(sanitize(input.evidence)),
        });
      });
    } catch (e) {
      ruleFailed = true;
      errors.push(`Rule ${rule.id} failed: ${(e as Error).message}`);
    }
  }

  findings.sort(
    (a, b) =>
      severityRank(b.severity) - severityRank(a.severity) ||
      a.file.localeCompare(b.file) ||
      (a.line ?? 0) - (b.line ?? 0) ||
      a.ruleId.localeCompare(b.ruleId),
  );

  const { score, grade } = grade_(findings);
  const agents = [...new Set<Agent>(ctx.files.filter((f) => f.kind !== 'binary').map((f) => f.agent))];
  return {
    version: VERSION,
    roots: paths,
    findings,
    stats: {
      files: ctx.files.length,
      servers: ctx.servers.length,
      skills: ctx.files.filter((f) => f.kind === 'skill').length,
      hooks: ctx.hooks.length,
      agents,
      durationMs: Date.now() - started,
    },
    score,
    grade,
    errors,
    suppressed,
    incomplete: found.truncated || ruleFailed,
  };
}

/**
 * Score out of 100. Each rule's contribution is capped so that one noisy rule (say, ten
 * unpinned servers) cannot dominate the grade. Any critical finding means F.
 */
function grade_(findings: Finding[]): { score: number; grade: ScanResult['grade'] } {
  const perRule = new Map<string, number>();
  for (const f of findings) {
    const w = WEIGHTS[f.severity];
    const cap = w * 3;
    perRule.set(f.ruleId, Math.min(cap, (perRule.get(f.ruleId) ?? 0) + w));
  }
  const penalty = [...perRule.values()].reduce((a, b) => a + b, 0);
  const score = Math.max(0, 100 - penalty);
  if (findings.some((f) => f.severity === 'critical')) return { score: Math.min(score, 39), grade: 'F' };
  const grade = score >= 90 ? 'A' : score >= 75 ? 'B' : score >= 60 ? 'C' : score >= 40 ? 'D' : 'F';
  return { score, grade };
}

const FIXTURE_DIR = /(^|\/)(tests?|__tests__|fixtures?|__fixtures__|testdata|test-data|spec|specs|evals?)\//i;

function isFixture(displayPath: string): boolean {
  return FIXTURE_DIR.test(displayPath);
}

function lower(s: Severity): Severity {
  const order: Severity[] = ['info', 'low', 'medium', 'high', 'critical'];
  return order[Math.max(0, order.indexOf(s) - 1)]!;
}

const IGNORE_RE = /bekci-ignore(?:-next-line)?(?:\s+([A-Z]{2}\d{3}(?:\s*,\s*[A-Z]{2}\d{3})*))?/;

function isSuppressed(content: string, line: number, ruleId: string): boolean {
  const check = (text: string, nextLineOnly: boolean) => {
    const m = IGNORE_RE.exec(text);
    if (!m) return false;
    if (nextLineOnly !== m[0].includes('next-line')) return false;
    if (!m[1]) return true;
    return m[1].split(/\s*,\s*/).includes(ruleId);
  };
  return check(lineText(content, line), false) || (line > 1 && check(lineText(content, line - 1), true));
}
