import path from 'node:path';
import { parseFrontmatter, toolList } from '../parsers/frontmatter.js';
import type { Rule, SourceFile } from '../types.js';
import { excerpt, lineOf } from '../util/text.js';

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);

function claudeSettings(files: SourceFile[]): Array<{ file: SourceFile; data: Obj }> {
  return files
    .filter((f) => f.kind === 'config' && f.agent === 'claude-code' && /^settings(\.local)?\.json$/.test(path.basename(f.path)))
    .filter((f) => isObj(f.data))
    .map((f) => ({ file: f, data: f.data as Obj }));
}

function allowList(data: Obj): string[] {
  const perms = data.permissions;
  if (!isObj(perms) || !Array.isArray(perms.allow)) return [];
  return perms.allow.filter((x): x is string => typeof x === 'string');
}

export const permissionBypass: Rule = {
  id: 'BK101',
  name: 'permission-bypass',
  title: 'Agent approvals or sandbox disabled',
  severity: 'high',
  category: 'permissions',
  description:
    'The agent is configured to run tools without asking: Claude Code `defaultMode: "bypassPermissions"`, ' +
    'Codex `approval_policy = "never"` with `sandbox_mode = "danger-full-access"`, and similar. A single prompt ' +
    'injection then turns directly into command execution on your machine.',
  remediation:
    'Use the default approval mode, or an allowlist of specific commands. If you need unattended runs, do it in ' +
    'a container or VM without your credentials.',
  references: ['https://docs.anthropic.com/en/docs/claude-code/security'],
  run(ctx, report) {
    for (const { file, data } of claudeSettings(ctx.files)) {
      const perms = isObj(data.permissions) ? data.permissions : {};
      if (perms.defaultMode === 'bypassPermissions') {
        report({
          file,
          line: lineOf(file.content, ['bypassPermissions']),
          message: 'Claude Code defaultMode is "bypassPermissions": every tool call runs without approval',
        });
      }
      if (data.skipDangerousModePermissionPrompt === true) {
        report({
          file,
          line: lineOf(file.content, ['skipDangerousModePermissionPrompt']),
          severity: 'medium',
          message: 'Warning prompt for dangerous permission mode is disabled',
        });
      }
    }

    for (const file of ctx.files) {
      if (file.kind !== 'config' || file.agent !== 'codex' || !isObj(file.data)) continue;
      const profiles: Array<[string, Obj]> = [['', file.data]];
      if (isObj(file.data.profiles)) {
        for (const [name, p] of Object.entries(file.data.profiles)) if (isObj(p)) profiles.push([name, p]);
      }
      for (const [name, p] of profiles) {
        const label = name ? `Codex profile "${name}"` : 'Codex';
        const never = p.approval_policy === 'never';
        const full = p.sandbox_mode === 'danger-full-access';
        const needle = name ? `[profiles.${name}]` : full ? 'danger-full-access' : 'approval_policy';
        if (never && full) {
          report({
            file,
            line: lineOf(file.content, [needle, 'danger-full-access']),
            message: `${label} runs commands with no approval and no sandbox (approval_policy = "never", sandbox_mode = "danger-full-access")`,
          });
        } else if (full) {
          report({
            file,
            line: lineOf(file.content, [needle, 'danger-full-access']),
            severity: 'medium',
            message: `${label} disables the sandbox (sandbox_mode = "danger-full-access")`,
          });
        }
      }
    }
  },
};

const UNRESTRICTED_BASH = /^(Bash|Shell|run_shell_command)(\(\s*\**\s*(:\s*\**\s*)?\))?$/i;

export const unrestrictedShell: Rule = {
  id: 'BK102',
  name: 'unrestricted-shell',
  title: 'Shell access allowed without approval',
  severity: 'high',
  category: 'permissions',
  description:
    'A permission allowlist pre-approves every shell command (`"Bash"`, `"Bash(*)"`). Any instruction the model ' +
    'picks up from a web page, issue, README or tool result can now run arbitrary commands without you seeing them.',
  remediation: 'Allow specific commands only, e.g. `"Bash(npm run test:*)"`, `"Bash(git status)"`.',
  run(ctx, report) {
    for (const { file, data } of claudeSettings(ctx.files)) {
      for (const entry of allowList(data)) {
        if (UNRESTRICTED_BASH.test(entry.trim())) {
          report({
            file,
            line: lineOf(file.content, [`"${entry}"`]),
            message: `Allowlist entry "${entry}" pre-approves every shell command`,
          });
        }
      }
    }
  },
};

const RISKY_COMMANDS: Record<string, string> = {
  curl: 'arbitrary network requests (data exfiltration)',
  wget: 'arbitrary downloads and uploads',
  nc: 'raw network connections',
  ncat: 'raw network connections',
  socat: 'raw network connections',
  ssh: 'remote shell access',
  scp: 'copying files to remote hosts',
  rsync: 'copying files to remote hosts',
  sudo: 'root privileges',
  su: 'switching users',
  rm: 'deleting arbitrary files',
  chmod: 'changing file permissions',
  chown: 'changing file ownership',
  dd: 'raw disk writes',
  eval: 'arbitrary code execution',
  exec: 'arbitrary code execution',
  sh: 'arbitrary shell scripts',
  bash: 'arbitrary shell scripts',
  zsh: 'arbitrary shell scripts',
  python: 'arbitrary code execution',
  python3: 'arbitrary code execution',
  node: 'arbitrary code execution',
  deno: 'arbitrary code execution',
  bun: 'arbitrary code execution',
  perl: 'arbitrary code execution',
  ruby: 'arbitrary code execution',
  php: 'arbitrary code execution',
  osascript: 'controlling other macOS apps',
  npx: 'running any package from the registry',
  bunx: 'running any package from the registry',
  uvx: 'running any package from the registry',
  pipx: 'running any package from the registry',
  docker: 'container control (often root-equivalent)',
  kubectl: 'cluster-wide changes',
  aws: 'changing cloud resources',
  gcloud: 'changing cloud resources',
  az: 'changing cloud resources',
  security: 'reading the macOS Keychain',
  'git push': 'publishing code to remotes',
  'gh api': 'arbitrary authenticated GitHub API calls',
  'npm publish': 'publishing packages',
};

/** Risky even with a fixed prefix, because appended arguments change what they do. */
const ALWAYS_RISKY = new Set([
  'curl',
  'wget',
  'nc',
  'ncat',
  'socat',
  'ssh',
  'scp',
  'rsync',
  'sudo',
  'su',
  'eval',
  'exec',
  'security',
]);

export const riskyAllowRule: Rule = {
  id: 'BK103',
  name: 'risky-allow-rule',
  title: 'Broad allowlist entry for a dangerous command',
  severity: 'medium',
  category: 'permissions',
  description:
    'A wildcard allowlist entry pre-approves a command that can reach the network, run arbitrary code or ' +
    'modify the system (`Bash(curl:*)`, `Bash(python3 *)`, `Bash(rm:*)`). Wildcards on these commands are ' +
    'equivalent to unrestricted shell access for an attacker who controls the arguments.',
  remediation: 'Replace the wildcard with the exact invocations you need, or leave the command on ask.',
  run(ctx, report) {
    for (const { file, data } of claudeSettings(ctx.files)) {
      for (const entry of allowList(data)) {
        const m = /^Bash\((.*)\)$/.exec(entry.trim());
        if (!m) continue;
        const spec = m[1]!.trim();
        if (!/[*]/.test(spec) || UNRESTRICTED_BASH.test(entry.trim())) continue;
        const words = spec
          .replace(/:\*$/, ' *')
          .split(/[\s:]+/)
          .filter(Boolean);
        const two = words.slice(0, 2).join(' ');
        const one = words[0] ?? '';
        // `npx tsc *` is scoped to one tool; `npx *`, `python3 -c *` and `curl <anything>` are not.
        const open = words[1] === undefined || words[1] === '*' || words[1]!.startsWith('-');
        const reason = RISKY_COMMANDS[two] ?? (ALWAYS_RISKY.has(one) || open ? RISKY_COMMANDS[one] : undefined);
        if (!reason) continue;
        report({ file, line: lineOf(file.content, [`"${entry}"`]), message: `"${entry}" pre-approves ${reason}` });
      }
    }
  },
};

export const autoApprovedMcp: Rule = {
  id: 'BK104',
  name: 'auto-approved-mcp',
  title: 'MCP servers or tools approved automatically',
  severity: 'medium',
  category: 'permissions',
  description:
    'MCP servers are trusted or their tools approved without confirmation: Claude Code ' +
    '`enableAllProjectMcpServers`, Gemini CLI `trust: true`, Cline/Roo `alwaysAllow`/`autoApprove`. A project ' +
    'you clone can then start servers you never reviewed, and tool calls driven by injected content run silently.',
  remediation: 'Approve servers individually and keep tool confirmations on for tools that write, send or execute.',
  run(ctx, report) {
    for (const { file, data } of claudeSettings(ctx.files)) {
      if (data.enableAllProjectMcpServers === true) {
        report({
          file,
          line: lineOf(file.content, ['enableAllProjectMcpServers']),
          message: 'enableAllProjectMcpServers starts every MCP server defined in any project .mcp.json without asking',
        });
      }
    }
    for (const s of ctx.servers) {
      if (s.raw.trust === true) {
        report({
          file: s.file,
          line: s.line,
          severity: 'high',
          message: `MCP server "${s.name}" has trust: true, bypassing all tool-call confirmations`,
        });
      }
      for (const key of ['alwaysAllow', 'autoApprove']) {
        const v = s.raw[key];
        if ((Array.isArray(v) && v.length > 0) || v === true) {
          const tools = Array.isArray(v) ? v.map(String) : ['all tools'];
          report({
            file: s.file,
            line: s.line,
            message: `MCP server "${s.name}" auto-approves ${tools.length > 3 ? `${tools.length} tools` : tools.join(', ')} (${key})`,
          });
        }
      }
    }
  },
};

export const projectHooks: Rule = {
  id: 'BK105',
  name: 'project-hook',
  title: 'Repository defines hooks that run automatically',
  severity: 'low',
  category: 'permissions',
  description:
    'A project-level config file registers shell commands that the agent runs automatically on events ' +
    '(session start, before/after tool calls). Anyone who can commit to the repository can change what runs on ' +
    'your machine when you open it with the agent. Review these before trusting a cloned repository.',
  remediation: 'Read every hook command. Prefer checked-in scripts over inline shell, and review hook changes in code review.',
  run(ctx, report) {
    for (const h of ctx.hooks) {
      if (h.file.scope !== 'project' || h.event === 'statusLine') continue;
      report({ file: h.file, line: h.line, message: `${h.event} hook runs: \`${excerpt(h.command, 80)}\`` });
    }
  },
};

export const skillUnrestrictedTools: Rule = {
  id: 'BK106',
  name: 'skill-unrestricted-tools',
  title: 'Skill or command pre-approves unrestricted shell',
  severity: 'medium',
  category: 'permissions',
  description:
    'A skill or slash command pre-approves unrestricted shell access via `allowed-tools` (e.g. `Bash` without a ' +
    'command pattern). While it is active, any command runs without asking you first.',
  remediation: 'Restrict the tool to the commands the skill needs, e.g. `Bash(python scripts/convert.py:*)`.',
  run(ctx, report) {
    for (const file of ctx.files) {
      // Sub-agent `tools:` only limits availability; `allowed-tools` in skills and commands
      // skips the permission prompt, which is what matters here.
      if (file.kind !== 'skill' && file.kind !== 'command') continue;
      const fm = parseFrontmatter(file.content);
      if (!fm) continue;
      const key = 'allowed-tools';
      const tools = toolList(fm.fields[key]);
      const broad = tools.find((t) => UNRESTRICTED_BASH.test(t.trim()));
      if (broad) {
        report({ file, line: fm.lines[key], message: `${key} grants unrestricted "${broad}"` });
      }
    }
  },
};

const GREEDY: RegExp[] = [
  /\b(always|must)\s+(be\s+)?(use|used|invoke|invoked|load|loaded|activate|activated|run|call|trigger)\w*\b[^.]{0,40}\b(every|any|all|each)\b/i,
  /\b(use|invoke|load|activate|trigger)\w*\s+(this|it)\s+(skill\s+)?(for|on|in|with|before)\s+(every|any|all|each)\s+(task|request|message|prompt|conversation|question|response|turn|interaction|action)/i,
  /\bbefore\s+(any|every|each)\s+(response|reply|action|answer|tool\s+call)\b/i,
  /\b(EXTREMELY|ABSOLUTELY)[\s_-]+(IMPORTANT|CRITICAL|MUST|REQUIRED)\b/,
  /\b(regardless|irrespective)\s+of\s+(the\s+)?(task|request|user|context)\b/i,
];

export const greedySkillTrigger: Rule = {
  id: 'BK107',
  name: 'greedy-skill-trigger',
  title: 'Skill description tries to trigger on everything',
  severity: 'low',
  category: 'prompt-injection',
  description:
    'Agents decide which skill to load from its `description`. A description that demands to be used for every ' +
    'task ("always invoke before any response") pulls the skill into unrelated conversations, which widens the ' +
    'blast radius of anything else the skill contains. Malicious skills use this to make sure they run.',
  remediation: 'Describe concretely when the skill applies. Only workflow skills you fully trust should be this broad.',
  run(ctx, report) {
    for (const file of ctx.files) {
      if (file.kind !== 'skill') continue;
      const fm = parseFrontmatter(file.content);
      const desc = fm?.fields.description;
      if (typeof desc !== 'string') continue;
      const m = GREEDY.map((re) => re.exec(desc)).find(Boolean);
      if (m)
        report({ file, line: fm!.lines.description, message: `Description asks to be used broadly: "${excerpt(m[0], 70)}"` });
    }
  },
};
