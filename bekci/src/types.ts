export const SEVERITIES = ['info', 'low', 'medium', 'high', 'critical'] as const;
export type Severity = (typeof SEVERITIES)[number];

export function severityRank(s: Severity): number {
  return SEVERITIES.indexOf(s);
}

export function isSeverity(s: string): s is Severity {
  return (SEVERITIES as readonly string[]).includes(s);
}

export type Agent =
  'claude-code' | 'claude-desktop' | 'cursor' | 'codex' | 'gemini-cli' | 'windsurf' | 'vscode' | 'copilot' | 'cline' | 'generic';

export const AGENT_LABELS: Record<Agent, string> = {
  'claude-code': 'Claude Code',
  'claude-desktop': 'Claude Desktop',
  cursor: 'Cursor',
  codex: 'Codex',
  'gemini-cli': 'Gemini CLI',
  windsurf: 'Windsurf',
  vscode: 'VS Code',
  copilot: 'GitHub Copilot',
  cline: 'Cline',
  generic: 'Shared (AGENTS.md / Agent Skills)',
};

/**
 * - instructions: always-on context files (CLAUDE.md, AGENTS.md, .cursorrules, ...)
 * - skill:        SKILL.md files
 * - subagent:     sub-agent definitions (.claude/agents/*.md)
 * - command:      slash commands / prompt files
 * - reference:    other text files inside a skill or imported by an instruction file
 * - script:       executable files shipped inside a skill
 * - config:       JSON/TOML agent configuration (MCP servers, permissions, hooks)
 * - binary:       opaque binaries shipped inside a skill (content not loaded)
 */
export type FileKind = 'instructions' | 'skill' | 'subagent' | 'command' | 'reference' | 'script' | 'config' | 'binary';

export const MARKDOWN_KINDS: ReadonlySet<FileKind> = new Set(['instructions', 'skill', 'subagent', 'command', 'reference']);

export type Scope = 'user' | 'project';

export interface SourceFile {
  /** Absolute path on disk. */
  path: string;
  /** Path shown to humans: `~/...` for user files, relative to the scan root otherwise. */
  displayPath: string;
  kind: FileKind;
  agent: Agent;
  scope: Scope;
  content: string;
  /** Parsed JSON/TOML for config files. */
  data?: unknown;
  parseError?: string;
  /** Directory of the skill this file belongs to (skill + script + binary kinds). */
  skillRoot?: string;
  /**
   * When false the raw text is not pattern-scanned (e.g. ~/.claude.json, which also stores
   * prompt history). Only the structured parts (MCP servers) are analysed.
   */
  rawScan: boolean;
  /** Size in bytes when the file exceeded the scan limit and its content was not read. */
  tooLarge?: number;
  /**
   * Third-party content: inside a skill or plugin directory. Such files may not silence their
   * own findings with inline comments.
   */
  vendored?: boolean;
  /** How many `@import` hops led to this file (0 or undefined for files found directly). */
  importDepth?: number;
}

export interface McpServer {
  name: string;
  file: SourceFile;
  agent: Agent;
  scope: Scope;
  transport: 'stdio' | 'http';
  command?: string;
  args: string[];
  env: Record<string, string>;
  url?: string;
  headers: Record<string, string>;
  raw: Record<string, unknown>;
  /** Shell wrapper the server is launched through (`cmd /c`, `sh -c`), if any. */
  launcher?: string;
  /** The command line exactly as configured, before unwrapping (used for content rules). */
  commandLine: string;
  /** Project path for per-project servers stored in a user-level file (~/.claude.json). */
  project?: string;
  /** 1-based line of the server definition inside its config file. */
  line: number;
  disabled: boolean;
}

export interface HookCommand {
  file: SourceFile;
  event: string;
  command: string;
  line: number;
}

export type Category =
  'prompt-injection' | 'code-execution' | 'exfiltration' | 'secrets' | 'permissions' | 'supply-chain' | 'toxic-flow';

export interface RuleMeta {
  id: string;
  name: string;
  title: string;
  severity: Severity;
  category: Category;
  description: string;
  remediation: string;
  references?: string[];
}

export interface Finding {
  ruleId: string;
  ruleName: string;
  category: Category;
  severity: Severity;
  title: string;
  message: string;
  /** Display path. */
  file: string;
  absPath: string;
  agent: Agent;
  line?: number;
  column?: number;
  /** Short, already-redacted excerpt. */
  evidence?: string;
}

export interface ScanContext {
  files: SourceFile[];
  servers: McpServer[];
  hooks: HookCommand[];
}

export interface ReportInput {
  file: SourceFile;
  message: string;
  line?: number;
  column?: number;
  evidence?: string;
  severity?: Severity;
}

export type Report = (input: ReportInput) => void;

export interface Rule extends RuleMeta {
  run(ctx: ScanContext, report: Report): void;
}

export interface ScanStats {
  files: number;
  servers: number;
  skills: number;
  hooks: number;
  agents: Agent[];
  durationMs: number;
}

export interface ScanResult {
  version: string;
  roots: string[];
  findings: Finding[];
  stats: ScanStats;
  score: number;
  grade: 'A' | 'B' | 'C' | 'D' | 'F';
  errors: string[];
  /** Some files could not be examined (file limit reached). Do not treat the result as clean. */
  incomplete: boolean;
}
