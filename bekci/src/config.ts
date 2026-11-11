import { promises as fs } from 'node:fs';
import path from 'node:path';
import { parseJsonc } from './parsers/jsonc.js';
import { isSeverity, type Severity } from './types.js';

export interface BekciConfig {
  /** Rule IDs or names to disable. */
  ignore?: string[];
  /** Globs (relative to the scan root, or `~/...`) whose findings are dropped. */
  ignorePaths?: string[];
  minSeverity?: Severity;
  failOn?: Severity | 'none';
}

export const CONFIG_FILES = ['.bekci.json', 'bekci.config.json'];

export async function loadConfig(dir: string): Promise<{ config: BekciConfig; path?: string }> {
  for (const name of CONFIG_FILES) {
    const p = path.join(dir, name);
    let text: string;
    try {
      text = await fs.readFile(p, 'utf8');
    } catch {
      continue;
    }
    const raw = parseJsonc(text) as Record<string, unknown>;
    return { config: validate(raw, p), path: p };
  }
  return { config: {} };
}

function validate(raw: Record<string, unknown>, file: string): BekciConfig {
  const out: BekciConfig = {};
  const fail = (msg: string): never => {
    throw new Error(`${file}: ${msg}`);
  };
  if (raw.ignore !== undefined) {
    if (!Array.isArray(raw.ignore) || raw.ignore.some((x) => typeof x !== 'string'))
      fail('"ignore" must be an array of rule IDs');
    out.ignore = raw.ignore as string[];
  }
  if (raw.ignorePaths !== undefined) {
    if (!Array.isArray(raw.ignorePaths) || raw.ignorePaths.some((x) => typeof x !== 'string'))
      fail('"ignorePaths" must be an array of globs');
    out.ignorePaths = raw.ignorePaths as string[];
  }
  if (raw.minSeverity !== undefined) {
    if (typeof raw.minSeverity !== 'string' || !isSeverity(raw.minSeverity))
      fail('"minSeverity" must be info|low|medium|high|critical');
    out.minSeverity = raw.minSeverity as Severity;
  }
  if (raw.failOn !== undefined) {
    if (typeof raw.failOn !== 'string' || !(raw.failOn === 'none' || isSeverity(raw.failOn)))
      fail('"failOn" must be a severity or "none"');
    out.failOn = raw.failOn as Severity | 'none';
  }
  return out;
}
