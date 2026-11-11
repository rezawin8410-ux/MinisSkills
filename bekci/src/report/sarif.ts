import { statSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { RULES } from '../rules/index.js';
import type { ScanOutput } from '../scan.js';
import type { Severity } from '../types.js';

const LEVEL: Record<Severity, 'error' | 'warning' | 'note'> = {
  critical: 'error',
  high: 'error',
  medium: 'warning',
  low: 'note',
  info: 'note',
};

/** GitHub code scanning maps this to Critical / High / Medium / Low. */
const SECURITY_SEVERITY: Record<Severity, string> = {
  critical: '9.5',
  high: '8.0',
  medium: '5.5',
  low: '3.0',
  info: '0.0',
};

export function renderSarif(result: ScanOutput, root: string = process.cwd()): string {
  let absRoot = path.resolve(root);
  // A single-file target: locations are relative to its directory.
  if (statSync(absRoot, { throwIfNoEntry: false })?.isFile()) absRoot = path.dirname(absRoot);
  const rules = RULES.map((r) => ({
    id: r.id,
    name: r.name,
    shortDescription: { text: r.title },
    fullDescription: { text: r.description },
    help: {
      text: `${r.description}\n\nFix: ${r.remediation}`,
      markdown: `${r.description}\n\n**Fix:** ${r.remediation}${r.references?.length ? `\n\n${r.references.map((u) => `- ${u}`).join('\n')}` : ''}`,
    },
    helpUri: `https://github.com/YusufSizmaz/bekci/blob/main/docs/rules.md#${r.id.toLowerCase()}`,
    defaultConfiguration: { level: LEVEL[r.severity] },
    properties: {
      tags: ['security', r.category],
      precision: 'high',
      'security-severity': SECURITY_SEVERITY[r.severity],
    },
  }));
  const index = new Map(RULES.map((r, i) => [r.id, i]));

  const results = result.findings.map((f) => {
    const inside = f.absPath === absRoot || f.absPath.startsWith(absRoot + path.sep);
    const artifactLocation = inside
      ? { uri: path.relative(absRoot, f.absPath).split(path.sep).join('/'), uriBaseId: '%SRCROOT%' }
      : { uri: pathToFileURL(f.absPath).href };
    return {
      ruleId: f.ruleId,
      ruleIndex: index.get(f.ruleId),
      level: LEVEL[f.severity],
      message: { text: f.message },
      locations: [
        {
          physicalLocation: {
            artifactLocation,
            ...(f.line ? { region: { startLine: f.line, ...(f.column ? { startColumn: f.column } : {}) } } : {}),
          },
        },
      ],
      partialFingerprints: { 'bekci/v1': `${f.ruleId}:${f.file}:${f.message}` },
      properties: { 'security-severity': SECURITY_SEVERITY[f.severity], agent: f.agent },
    };
  });

  const sarif = {
    $schema: 'https://json.schemastore.org/sarif-2.1.0.json',
    version: '2.1.0',
    runs: [
      {
        tool: {
          driver: {
            name: 'bekci',
            semanticVersion: result.version,
            informationUri: 'https://github.com/YusufSizmaz/bekci',
            rules,
          },
        },
        originalUriBaseIds: { '%SRCROOT%': { uri: pathToFileURL(absRoot + path.sep).href } },
        results,
      },
    ],
  };
  return JSON.stringify(sarif, null, 2);
}
