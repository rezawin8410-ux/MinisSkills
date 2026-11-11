import os from 'node:os';
import path from 'node:path';
import type { McpServer, Rule } from '../types.js';
import { levenshtein } from '../util/text.js';

export interface PackageRef {
  ecosystem: 'npm' | 'pypi' | 'container' | 'git';
  runner: string;
  spec: string;
  name: string;
  version?: string;
}

const NPM_RUNNERS = new Set(['npx', 'bunx', 'pnpx']);
/** Flags that take a value, per runner family. */
const NPX_VALUE_FLAGS = new Set([
  '-p',
  '--package',
  '--registry',
  '--cache',
  '--userconfig',
  '-c',
  '--call',
  '-w',
  '--workspace',
]);
const UVX_VALUE_FLAGS = new Set([
  '--from',
  '--with',
  '-w',
  '--python',
  '-p',
  '--index',
  '--index-url',
  '--extra-index-url',
  '--default-index',
  '--with-requirements',
  '--with-editable',
  '--constraint',
]);
const DOCKER_VALUE_FLAGS = new Set([
  '-e',
  '--env',
  '-v',
  '--volume',
  '--name',
  '-p',
  '--publish',
  '--network',
  '--net',
  '--mount',
  '-w',
  '--workdir',
  '-u',
  '--user',
  '--entrypoint',
  '--env-file',
  '-l',
  '--label',
  '--platform',
  '-m',
  '--memory',
  '--cpus',
  '--add-host',
  '--cap-add',
  '--cap-drop',
  '--security-opt',
  '--pid',
  '--ipc',
  '--device',
  '--tmpfs',
  '--hostname',
  '-h',
  '--runtime',
  '--pull',
  '--ulimit',
  '--log-driver',
  '--restart',
  '--group-add',
  '--dns',
  '--shm-size',
]);

function bin(command: string): string {
  return path
    .basename(command)
    .replace(/\.(cmd|exe|bat|ps1)$/i, '')
    .toLowerCase();
}

function firstPositional(args: string[], valueFlags: Set<string>): { value?: string; fromFlag?: string } {
  for (let i = 0; i < args.length; i++) {
    const a = args[i]!;
    if (a === '--') return { value: args[i + 1] };
    if (a.startsWith('-')) {
      const [flag, inline] = a.split('=', 2);
      if ((flag === '-p' || flag === '--package' || flag === '--from') && (inline ?? args[i + 1])) {
        return { fromFlag: inline ?? args[i + 1] };
      }
      if (inline === undefined && valueFlags.has(flag!)) i++;
      continue;
    }
    return { value: a };
  }
  return {};
}

export function packageRef(server: McpServer): PackageRef | undefined {
  if (!server.command) return undefined;
  const runner = bin(server.command);
  let args = server.args;

  if (runner === 'npm' && args[0] === 'exec') args = args.slice(1);
  else if ((runner === 'pnpm' || runner === 'yarn') && args[0] === 'dlx') args = args.slice(1);
  else if (runner === 'uv' && args[0] === 'tool' && args[1] === 'run') args = args.slice(2);
  else if (runner === 'pipx' && args[0] === 'run') args = args.slice(1);
  else if ((runner === 'docker' || runner === 'podman') && args[0] === 'run') args = args.slice(1);
  else if (!NPM_RUNNERS.has(runner) && runner !== 'uvx') return undefined;

  if (runner === 'docker' || runner === 'podman') {
    const { value } = firstPositional(args, DOCKER_VALUE_FLAGS);
    if (!value) return undefined;
    const digest = value.includes('@sha256:');
    const lastSeg = value.split('/').pop() ?? value;
    const tag = digest ? value.split('@')[1] : lastSeg.includes(':') ? lastSeg.split(':').pop() : undefined;
    return { ecosystem: 'container', runner, spec: value, name: value.replace(/[:@].*$/, ''), version: tag };
  }

  const python = runner === 'uvx' || runner === 'uv' || runner === 'pipx';
  const { value, fromFlag } = firstPositional(args, python ? UVX_VALUE_FLAGS : NPX_VALUE_FLAGS);
  const spec = fromFlag ?? value;
  if (!spec) return undefined;

  if (/^(git\+|github:|gitlab:|bitbucket:|https?:\/\/|git@)/.test(spec)) {
    const commit = /#([0-9a-f]{40})$|@([0-9a-f]{40})$/.exec(spec);
    return { ecosystem: 'git', runner, spec, name: spec, version: commit ? (commit[1] ?? commit[2]) : undefined };
  }
  if (/^(\.{1,2}[/\\]|\/|~|[A-Za-z]:\\|file:)/.test(spec)) return undefined; // local path

  if (python) {
    const m = /^([A-Za-z0-9_.\-[\],]+?)(?:(==|@|===)([^\s]+))?$/.exec(spec);
    if (!m) return undefined;
    return { ecosystem: 'pypi', runner, spec, name: m[1]!.replace(/\[.*\]$/, '').toLowerCase(), version: m[3] };
  }
  const m = /^(@[^/@\s]+\/[^@\s]+|[^@\s]+)(?:@(.+))?$/.exec(spec);
  if (!m) return undefined;
  return { ecosystem: 'npm', runner, spec, name: m[1]!.toLowerCase(), version: m[2] };
}

function isPinned(ref: PackageRef): boolean {
  const v = ref.version;
  if (!v) return false;
  if (ref.ecosystem === 'container') return v.startsWith('sha256:') || (v !== 'latest' && /\d/.test(v));
  if (ref.ecosystem === 'git') return /^[0-9a-f]{40}$/.test(v);
  return /^v?\d+\.\d+(\.\d+)?([-+][\w.-]+)?$/.test(v);
}

export const unpinnedPackage: Rule = {
  id: 'BK201',
  name: 'unpinned-mcp-package',
  title: 'MCP server runs an unpinned package',
  severity: 'medium',
  category: 'supply-chain',
  description:
    'The MCP server is launched with `npx`, `uvx`, `pipx run`, `docker run` or a git URL without an exact version. ' +
    'Every launch fetches whatever is newest, so a compromised maintainer account, a hijacked package or a ' +
    'malicious update ("rug pull") executes on your machine automatically, with your credentials in reach.',
  remediation:
    'Pin an exact version (`@scope/server@1.4.2`, `mcp-server-fetch==2025.4.7`, an image digest or a commit SHA) ' +
    'and update deliberately.',
  references: ['https://thehackernews.com/2025/09/first-malicious-mcp-server-found.html'],
  run(ctx, report) {
    for (const s of ctx.servers) {
      if (s.disabled) continue;
      const ref = packageRef(s);
      if (!ref || isPinned(ref)) continue;
      const how = ref.version
        ? `version "${ref.version}" is not an exact pin`
        : 'no version pinned, so the latest release runs on every start';
      report({ file: s.file, line: s.line, message: `MCP server "${s.name}" runs \`${ref.runner} ${ref.spec}\`: ${how}` });
    }
  },
};

const LOOPBACK = /^(localhost|127(\.\d+){3}|\[?::1\]?|0\.0\.0\.0|host\.docker\.internal|[\w-]+\.localhost)$/i;
const PRIVATE = /^(10(\.\d+){3}|192\.168(\.\d+){2}|172\.(1[6-9]|2\d|3[01])(\.\d+){2}|[\w-]+\.(local|lan|internal|home\.arpa))$/i;

export const insecureRemote: Rule = {
  id: 'BK202',
  name: 'insecure-remote-mcp',
  title: 'Remote MCP server over plain HTTP',
  severity: 'high',
  category: 'supply-chain',
  description:
    'A remote MCP server is reached over unencrypted `http://` or `ws://`. Anyone on the network path can read ' +
    'your prompts, tool results and tokens, or inject their own tool definitions and responses.',
  remediation: "Use the server's `https://` endpoint. Plain HTTP is only acceptable for loopback addresses.",
  run(ctx, report) {
    for (const s of ctx.servers) {
      if (!s.url || s.disabled) continue;
      let u: URL;
      try {
        u = new URL(s.url);
      } catch {
        continue;
      }
      if (u.protocol !== 'http:' && u.protocol !== 'ws:') continue;
      if (LOOPBACK.test(u.hostname)) continue;
      const priv = PRIVATE.test(u.hostname);
      report({
        file: s.file,
        line: s.line,
        severity: priv ? 'medium' : 'high',
        message: `MCP server "${s.name}" connects to ${u.protocol}//${u.host} without TLS${priv ? ' (private network)' : ''}`,
      });
    }
  },
};

export const privilegedContainer: Rule = {
  id: 'BK203',
  name: 'privileged-container',
  title: 'MCP server container escapes isolation',
  severity: 'high',
  category: 'supply-chain',
  description:
    'A Docker/Podman-based MCP server is started with options that remove container isolation: `--privileged`, ' +
    'host networking/PID namespace, dangerous capabilities, the Docker socket, or a mount of the host root or ' +
    'home directory. The server then has the same reach as a process on the host.',
  remediation: 'Drop the flag. Mount only the directory the server needs, read-only where possible (`-v "$PWD:/work:ro"`).',
  run(ctx, report) {
    for (const s of ctx.servers) {
      if (!s.command || !/^(docker|podman)$/.test(bin(s.command)) || s.disabled) continue;
      const args = s.args;
      const problems: string[] = [];
      for (let i = 0; i < args.length; i++) {
        const a = args[i]!;
        const next = args[i + 1] ?? '';
        const [flag, inline] = a.split('=', 2);
        const val = inline ?? next;
        if (a === '--privileged') problems.push('--privileged');
        if ((flag === '--network' || flag === '--net') && val === 'host') problems.push('host network');
        if ((flag === '--pid' || flag === '--ipc') && val === 'host') problems.push(`host ${flag.slice(2)} namespace`);
        if (flag === '--cap-add' && /^(ALL|SYS_ADMIN|SYS_PTRACE|NET_ADMIN|SYS_MODULE|DAC_READ_SEARCH)$/i.test(val))
          problems.push(`--cap-add ${val}`);
        if (flag === '--security-opt' && /(seccomp|apparmor)[=:]unconfined/.test(val)) problems.push(`--security-opt ${val}`);
        if (flag === '-v' || flag === '--volume' || flag === '--mount') {
          const source = flag === '--mount' ? (/(?:source|src)=([^,]+)/.exec(val)?.[1] ?? '') : (val.split(':')[0] ?? '');
          if (/docker\.sock$/.test(source)) problems.push('Docker socket mounted');
          else if (isRootOrHome(source)) problems.push(`host path "${source}" mounted`);
        }
      }
      if (problems.length) {
        report({ file: s.file, line: s.line, message: `MCP server "${s.name}" container: ${[...new Set(problems)].join(', ')}` });
      }
    }
  },
};

function isRootOrHome(p: string): boolean {
  const raw = p.trim().replace(/^["']|["']$/g, '');
  if (!raw) return false;
  const v = raw.replace(/[/\\]+$/, '') || '/';
  if (v === '/' || v === '~' || /^\$\{?HOME\}?$/.test(v) || /^%USERPROFILE%$/i.test(v)) return true;
  if (/^[A-Za-z]:$/.test(v)) return true;
  if (/^\/(Users|home)\/[^/]+$/.test(v) || /^[A-Za-z]:\\Users\\[^\\]+$/i.test(v)) return true;
  return v === os.homedir();
}

export const broadFilesystem: Rule = {
  id: 'BK204',
  name: 'broad-filesystem-access',
  title: 'MCP server granted the whole home or root directory',
  severity: 'medium',
  category: 'supply-chain',
  description:
    'An MCP server receives `/`, `~` or your home directory as an argument, typically a filesystem server. ' +
    'Everything under it, including SSH keys, cloud credentials, browser profiles and other projects, becomes ' +
    'readable (and often writable) through tool calls that a prompt injection can trigger.',
  remediation: 'Pass only the project directories the server needs.',
  run(ctx, report) {
    for (const s of ctx.servers) {
      if (s.disabled || !s.command || /^(docker|podman)$/.test(bin(s.command))) continue;
      const broad = s.args.filter((a) => isRootOrHome(a));
      if (broad.length) {
        report({
          file: s.file,
          line: s.line,
          message: `MCP server "${s.name}" is given ${broad.map((b) => `"${b}"`).join(', ')}`,
        });
      }
    }
  },
};

/** Well-known MCP packages; used to spot look-alike names. */
export const KNOWN_PACKAGES = [
  '@modelcontextprotocol/server-filesystem',
  '@modelcontextprotocol/server-github',
  '@modelcontextprotocol/server-gitlab',
  '@modelcontextprotocol/server-memory',
  '@modelcontextprotocol/server-everything',
  '@modelcontextprotocol/server-sequential-thinking',
  '@modelcontextprotocol/server-puppeteer',
  '@modelcontextprotocol/server-brave-search',
  '@modelcontextprotocol/server-google-maps',
  '@modelcontextprotocol/server-slack',
  '@modelcontextprotocol/server-postgres',
  '@modelcontextprotocol/server-redis',
  '@modelcontextprotocol/server-gdrive',
  '@modelcontextprotocol/server-aws-kb-retrieval',
  '@modelcontextprotocol/server-everart',
  '@modelcontextprotocol/inspector',
  '@playwright/mcp',
  '@upstash/context7-mcp',
  '@notionhq/notion-mcp-server',
  '@supabase/mcp-server-supabase',
  '@browserbasehq/mcp-server-browserbase',
  '@stripe/mcp',
  '@sentry/mcp-server',
  '@cloudflare/mcp-server-cloudflare',
  '@heroku/mcp-server',
  '@azure/mcp',
  '@brightdata/mcp',
  '@21st-dev/magic',
  '@executeautomation/playwright-mcp-server',
  '@wonderwhy-er/desktop-commander',
  '@agentdeskai/browser-tools-mcp',
  '@anthropic-ai/claude-code',
  '@openai/codex',
  '@google/gemini-cli',
  '@vercel/mcp-adapter',
  '@neondatabase/mcp-server-neon',
  '@shopify/dev-mcp',
  '@hubspot/mcp-server',
  '@mongodb-js/mongodb-mcp-server',
  '@elastic/mcp-server-elasticsearch',
  '@pinecone-database/mcp',
  '@kimtaeyoon83/mcp-server-youtube-transcript',
  '@benborla29/mcp-server-mysql',
  'firecrawl-mcp',
  'exa-mcp-server',
  'tavily-mcp',
  'figma-developer-mcp',
  'chrome-devtools-mcp',
  'mcp-remote',
  'task-master-ai',
  'context7-mcp',
  'mcp-server-fetch',
  'mcp-server-git',
  'mcp-server-time',
  'mcp-server-sqlite',
  'mcp-server-qdrant',
  'mcp-server-kubernetes',
  'mcp-server-docker',
  'mcp-atlassian',
  'serena',
  'markitdown-mcp',
  'awslabs.core-mcp-server',
  'awslabs.aws-documentation-mcp-server',
  'duckduckgo-mcp-server',
  'mcp-obsidian',
];

const TRUSTED_SCOPES = new Set(KNOWN_PACKAGES.filter((p) => p.startsWith('@')).map((p) => p.split('/')[0]!));
const KNOWN = new Set(KNOWN_PACKAGES);

export function lookalike(name: string): string | undefined {
  if (KNOWN.has(name)) return undefined;
  const scope = name.startsWith('@') ? name.split('/')[0]! : undefined;
  if (scope && TRUSTED_SCOPES.has(scope)) return undefined;
  if (scope) {
    for (const trusted of TRUSTED_SCOPES) {
      const d = levenshtein(scope, trusted, 2);
      if (d > 0 && d <= (trusted.length > 8 ? 2 : 1)) return trusted;
    }
  }
  for (const known of KNOWN_PACKAGES) {
    // Same official package name re-published under a different scope.
    if (scope && known.startsWith('@') && name.split('/')[1] === known.split('/')[1] && /^server-/.test(known.split('/')[1]!)) {
      return known;
    }
    const max = known.length >= 12 ? 2 : 1;
    if (Math.abs(known.length - name.length) > max) continue;
    const d = levenshtein(name, known, max);
    if (d > 0 && d <= max) return known;
  }
  return undefined;
}

export const typosquat: Rule = {
  id: 'BK205',
  name: 'typosquat-package',
  title: 'Look-alike of a well-known MCP package',
  severity: 'high',
  category: 'supply-chain',
  description:
    'The package name is one or two characters away from a popular MCP server, or reuses an official package ' +
    'name under a different scope. Typosquatting is the most common way malicious packages reach developer machines.',
  remediation: "Check the exact package name on the registry and in the vendor's documentation, then fix the config.",
  run(ctx, report) {
    for (const s of ctx.servers) {
      const ref = packageRef(s);
      if (!ref || (ref.ecosystem !== 'npm' && ref.ecosystem !== 'pypi')) continue;
      const target = lookalike(ref.name);
      if (target)
        report({
          file: s.file,
          line: s.line,
          message: `MCP server "${s.name}" runs "${ref.name}", which looks like "${target}"`,
        });
    }
  },
};

export const opaqueBinary: Rule = {
  id: 'BK206',
  name: 'opaque-binary',
  title: 'Skill ships a compiled binary',
  severity: 'medium',
  category: 'supply-chain',
  description:
    'A skill directory contains compiled code (executables, shared libraries, `.pyc`, `.jar`, `.wasm`). It cannot ' +
    'be reviewed like the rest of the skill, and anything in the skill may tell the agent to run it.',
  remediation: 'Prefer skills that ship source. Build binaries yourself from the published source, or remove the skill.',
  run(ctx, report) {
    for (const file of ctx.files) {
      if (file.kind !== 'binary') continue;
      report({ file, message: `Compiled file ${path.basename(file.path)} bundled with a skill` });
    }
  },
};

export const unscannableFile: Rule = {
  id: 'BK207',
  name: 'unscannable-file',
  title: 'Agent file too large to scan',
  severity: 'high',
  category: 'supply-chain',
  description:
    'A skill, instruction, script or config file is larger than the scan limit (2 MB), so its contents were not ' +
    'checked. Legitimate agent files are rarely this big; padding a file is a simple way to push a payload past ' +
    'scanners and human reviewers alike.',
  remediation: 'Open the file and find out why it is so large. Do not install it until you know what it contains.',
  run(ctx, report) {
    for (const file of ctx.files) {
      if (!file.tooLarge) continue;
      report({ file, message: `${(file.tooLarge / 1024 / 1024).toFixed(1)} MB ${file.kind} file was not scanned (limit 2 MB)` });
    }
  },
};

export const unparseableConfig: Rule = {
  id: 'BK208',
  name: 'unparseable-config',
  title: 'Agent config could not be analysed',
  severity: 'high',
  category: 'supply-chain',
  description:
    'An MCP, settings or hooks file failed to parse, so the servers, permissions and hooks it defines were not ' +
    'checked. If the agent reads the file with a more lenient parser, whatever it contains still takes effect. ' +
    'Reported as high in the scanned project and medium in your own user-level config.',
  remediation:
    'Fix the syntax error (the message shows where). If the file is valid for your agent, please report it as a bekci bug.',
  run(ctx, report) {
    for (const file of ctx.files) {
      if (file.kind !== 'config' || !file.parseError) continue;
      const line = /line (\d+)/.exec(file.parseError)?.[1];
      report({
        file,
        line: line ? Number(line) : undefined,
        severity: file.scope === 'project' ? 'high' : 'medium',
        message: `Not analysed: ${file.parseError}`,
      });
    }
  },
};
