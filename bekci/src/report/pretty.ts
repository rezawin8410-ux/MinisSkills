import { findRule } from '../rules/index.js';
import type { ScanOutput } from '../scan.js';
import { AGENT_LABELS, SEVERITIES, type Severity } from '../types.js';
import { c, severityBadge, severityColor } from '../util/ansi.js';

const INDENT = ' '.repeat(13);

export function renderPretty(result: ScanOutput, opts: { verbose?: boolean } = {}): string {
  const out: string[] = [];
  const { stats } = result;
  out.push('');
  out.push(`  ${c.bold('bekci')} ${c.dim(`v${result.version}`)} ${c.dim('\u00B7 security scan for AI agent setups')}`);
  out.push('');
  const agents = stats.agents.map((a) => AGENT_LABELS[a]).join(', ') || 'none found';
  out.push(
    `  ${c.dim('Scanned')} ${plural(stats.files, 'file')} ${c.dim('\u00B7')} ${plural(stats.servers, 'MCP server')} ${c.dim('\u00B7')} ` +
      `${plural(stats.skills, 'skill')} ${c.dim('\u00B7')} ${plural(stats.hooks, 'hook')} ${c.dim(`in ${stats.durationMs} ms`)}`,
  );
  out.push(`  ${c.dim('Agents ')} ${agents}`);
  out.push('');

  for (const f of result.findings) {
    const loc = f.line ? `${f.file}:${f.line}${f.column ? `:${f.column}` : ''}` : f.file;
    out.push(`  ${severityBadge(f.severity)} ${c.bold(f.ruleId)}  ${f.title}`);
    out.push(`${INDENT}${c.cyan(loc)}`);
    out.push(`${INDENT}${f.message}`);
    if (opts.verbose && f.evidence) out.push(`${INDENT}${c.dim('\u203A ' + f.evidence)}`);
    out.push('');
  }

  if (result.findings.length) {
    const fixes = [...new Set(result.findings.map((f) => f.ruleId))].map((id) => findRule(id)).filter((r) => r !== undefined);
    out.push(`  ${c.bold('How to fix')}`);
    for (const r of fixes) out.push(`  ${c.dim(r.id)}  ${firstSentence(r.remediation)}`);
    out.push('');
  }

  for (const e of result.errors) out.push(`  ${c.yellow('!')} ${c.dim(e)}`);
  if (result.errors.length) out.push('');

  out.push(`  ${c.dim('\u2500'.repeat(58))}`);
  const counts = SEVERITIES.slice()
    .reverse()
    .map((s) => [s, result.findings.filter((f) => f.severity === s).length] as [Severity, number])
    .filter(([, n]) => n > 0)
    .map(([s, n]) => severityColor(s)(`${n} ${s}`))
    .join(c.dim(' \u00B7 '));
  const gradeColor = { A: c.green, B: c.green, C: c.yellow, D: c.red, F: c.magenta }[result.grade];
  out.push(
    `  ${c.bold(gradeColor(`Grade ${result.grade}`))} ${c.dim(`(${result.score}/100)`)}   ` +
      (result.findings.length ? counts : result.incomplete ? c.yellow('Scan incomplete') : c.green('No issues found')),
  );
  if (result.suppressed) out.push(`  ${c.dim(`${result.suppressed} finding(s) suppressed by bekci-ignore comments`)}`);
  if (result.findings.length) {
    out.push(`  ${c.dim(`Details: bekci explain ${result.findings[0]!.ruleId}`)}`);
  }
  out.push('');
  return out.join('\n');
}

function plural(n: number, word: string): string {
  return `${c.bold(String(n))} ${word}${n === 1 ? '' : 's'}`;
}

function firstSentence(s: string): string {
  const m = /^.*?(?<!\b(?:e\.g|i\.e|etc|vs))[.!?](?=\s|$)/.exec(s);
  return m ? m[0] : s;
}
