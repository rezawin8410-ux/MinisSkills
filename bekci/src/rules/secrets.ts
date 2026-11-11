import type { Rule, SourceFile } from '../types.js';
import { MARKDOWN_KINDS } from '../types.js';
import { lineOf, positionAt, redact } from '../util/text.js';

export const SECRET_PATTERNS: Array<[RegExp, string]> = [
  [/\bsk-ant-(api|admin)\d{2}-[A-Za-z0-9_-]{80,}/g, 'Anthropic API key'],
  [/\bsk-(proj|svcacct|admin)-[A-Za-z0-9_-]{40,}/g, 'OpenAI API key'],
  [/\bsk-[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}\b/g, 'OpenAI API key'],
  [/\bgh[pousr]_[A-Za-z0-9]{36,}\b/g, 'GitHub token'],
  [/\bgithub_pat_[A-Za-z0-9_]{60,}\b/g, 'GitHub fine-grained token'],
  [/\bglpat-[A-Za-z0-9_-]{20,}\b/g, 'GitLab token'],
  [/\b(AKIA|ASIA)[0-9A-Z]{16}\b/g, 'AWS access key ID'],
  [/\bxox[abposr]-[A-Za-z0-9-]{10,}\b/g, 'Slack token'],
  [/\bAIza[0-9A-Za-z_-]{35}\b/g, 'Google API key'],
  [/\bGOCSPX-[A-Za-z0-9_-]{28}\b/g, 'Google OAuth client secret'],
  [/\b[rs]k_live_[0-9A-Za-z]{24,}\b/g, 'Stripe live key'],
  [/\bnpm_[A-Za-z0-9]{36}\b/g, 'npm token'],
  [/\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}/g, 'PyPI token'],
  [/\bhf_[A-Za-z0-9]{34,}\b/g, 'Hugging Face token'],
  [/\b(ntn_[A-Za-z0-9]{40,}|secret_[A-Za-z0-9]{43})\b/g, 'Notion token'],
  [/\blin_api_[A-Za-z0-9]{40}\b/g, 'Linear API key'],
  [/\bsbp_[a-f0-9]{40}\b/g, 'Supabase access token'],
  [/\bgsk_[A-Za-z0-9]{52}\b/g, 'Groq API key'],
  [/\bpplx-[A-Za-z0-9]{48}\b/g, 'Perplexity API key'],
  [/\bxai-[A-Za-z0-9]{70,}\b/g, 'xAI API key'],
  [/\bfc-[a-f0-9]{32}\b/g, 'Firecrawl API key'],
  [/\btvly-(dev-)?[A-Za-z0-9]{32}\b/g, 'Tavily API key'],
  [/\bdop_v1_[a-f0-9]{64}\b/g, 'DigitalOcean token'],
  [/\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b/g, 'SendGrid API key'],
  [/\bsntrys_[A-Za-z0-9_=+/-]{60,}/g, 'Sentry token'],
  [/https:\/\/hooks\.slack\.com\/services\/T[A-Z0-9]+\/B[A-Z0-9]+\/[A-Za-z0-9]{20,}/g, 'Slack webhook URL'],
  [/https:\/\/discord(app)?\.com\/api\/webhooks\/\d+\/[A-Za-z0-9_-]{50,}/g, 'Discord webhook URL'],
  [/\bshp(at|ca|pa|ss)_[a-fA-F0-9]{32}\b/g, 'Shopify access token'],
  [/\bATATT3[A-Za-z0-9_=-]{150,}/g, 'Atlassian API token'],
  [/\bdp\.(pt|st|sa|ct|scim|audit)\.[A-Za-z0-9]{40,}\b/g, 'Doppler token'],
  [/\bPMAK-[a-f0-9]{24}-[a-f0-9]{34}\b/g, 'Postman API key'],
  [/-----BEGIN ((RSA|DSA|EC|OPENSSH|PGP|ENCRYPTED) )?PRIVATE KEY( BLOCK)?-----(\\[rn]|\s)+[A-Za-z0-9+/=]{40,}/g, 'private key'],
  [/\b(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp):\/\/[^\s:@/'"]+:[^\s@/'"]{6,}@[^\s'"]+/g, 'database URL with password'],
];

const PLACEHOLDER =
  /x{4,}|X{4,}|\*{3,}|\.{3}|\u2026|<[^>]*>|\{\{|\$\{|your[_-]?|example|EXAMPLE|placeholder|changeme|dummy|sample|redacted|REDACTED|test[_-]?key|0{8,}|1234567/;

const DUMMY_DB_URL =
  /:\/\/[^:\s]+:[^@\s]*(pass|secret|pwd|changeme|example|test|1234)[^@\s]*@|:\/\/[^:\s]+:(postgres|mysql|root|admin|user)@|@(localhost|127\.0\.0\.1|db|postgres|mysql|redis|mongo|host|hostname|server|your[-_]?host)[:/]/i;

function isPlaceholder(s: string): boolean {
  // A token whose body is one repeated character (ghp_aaaa...) is test data. Only the whole body
  // counts: real private keys contain long runs such as AAAAAAAA.
  if (/^(.)\1{7,}$/.test(s.replace(/^[A-Za-z]+[_-]/, ''))) return true;
  return PLACEHOLDER.test(s) || (/^[a-z+]+:\/\//i.test(s) && DUMMY_DB_URL.test(s));
}

const INLINE_CREDENTIAL =
  /\b((?:bearer|token|api[_-]?key|apikey|secret|password|passwd|pwd|authorization)["']?\s*[:=]?\s*["']?(?:bearer\s+)?)([A-Za-z0-9_\-./+=]{12,})/gi;

/** Masks every recognisable secret in a human-readable string (messages, evidence). */
export function redactSecrets(text: string): string {
  let out = text;
  for (const [re] of SECRET_PATTERNS) {
    out = out.replace(new RegExp(re.source, re.flags), (m) => (isPlaceholder(m) ? m : redact(m)));
  }
  return out.replace(INLINE_CREDENTIAL, (m, prefix: string, value: string) =>
    /^\$|^\{\{|\*{8}/.test(value) || isPlaceholder(value) ? m : prefix + redact(value),
  );
}

export function matchesKnownSecret(value: string): string | undefined {
  for (const [re, what] of SECRET_PATTERNS) {
    const r = new RegExp(re.source, re.flags.replace('g', ''));
    const m = r.exec(value);
    if (m && !isPlaceholder(m[0])) return what;
  }
  return undefined;
}

export const hardcodedSecret: Rule = {
  id: 'BK011',
  name: 'hardcoded-secret',
  title: 'Hardcoded secret',
  severity: 'high',
  category: 'secrets',
  description:
    'A recognisable API key, token, private key or credentialed URL is stored in plain text in an agent config, ' +
    'instruction file or skill. Agent configs are copied between machines, committed to dotfile repos and read ' +
    'by every tool and extension that runs as you; instruction files are sent to the model provider.',
  remediation:
    'Revoke and rotate the credential, then reference it from the environment instead (`"env": { "TOKEN": "${TOKEN}" }`) ' +
    'or a secret manager reference such as `op://vault/item/field`.',
  run(ctx, report) {
    const scan = (file: SourceFile, text: string, fixedLine?: number) => {
      for (const [re, what] of SECRET_PATTERNS) {
        for (const m of text.matchAll(re)) {
          if (isPlaceholder(m[0])) continue;
          const pos = fixedLine ? { line: fixedLine, column: 1 } : positionAt(text, m.index!);
          // Database URLs in prose are nearly always examples; recognised token formats are not.
          const exampleUrl = what === 'database URL with password' && MARKDOWN_KINDS.has(file.kind);
          report({
            file,
            ...pos,
            severity: exampleUrl ? 'medium' : undefined,
            message: `${what} in plain text: ${redact(m[0])}`,
          });
        }
      }
    };
    for (const file of ctx.files) {
      if (file.kind === 'binary' || !file.rawScan) continue;
      scan(file, file.content);
    }
    // Files that are not raw-scanned (e.g. ~/.claude.json) still have their MCP servers checked.
    for (const s of ctx.servers) {
      if (s.file.rawScan) continue;
      scan(s.file, [s.commandLine, ...Object.values(s.env), ...Object.values(s.headers), s.url ?? ''].join('\n'), s.line);
    }
  },
};

const SENSITIVE_KEY =
  /(^|[_-])(API[_-]?KEY|APIKEY|TOKEN|ACCESS[_-]?TOKEN|AUTH[_-]?TOKEN|SECRET|CLIENT[_-]?SECRET|PASSWORD|PASSWD|PWD|PAT|PRIVATE[_-]?KEY|ACCESS[_-]?KEY|CREDENTIALS?|COOKIE|SESSION)($|[_-])/i;
const SENSITIVE_HEADER = /^(authorization|x-api-key|api-key|x-auth-token|cookie|x-access-token|private-token)$/i;
const REFERENCE = /^\s*(\$\{?[A-Za-z_]|\$env:|%[A-Za-z_]+%|\{\{|op:\/\/|env:|secret:|vault:|keychain:|bws:|awssm:|gcpsm:|\$\(|`)/;

function literalCredential(value: string): boolean {
  const v = value.trim();
  if (v.length < 8 || REFERENCE.test(v) || isPlaceholder(v)) return false;
  if (/^(true|false|null|none|\d+(\.\d+)?)$/i.test(v)) return false;
  if (/^(bearer|token|basic)\s+(\$|\{\{|<)/i.test(v)) return false;
  if (/^[a-z]+(-[a-z]+)*$/.test(v)) return false; // looks like an identifier, not a secret
  return true;
}

export const plaintextCredential: Rule = {
  id: 'BK012',
  name: 'plaintext-credential',
  title: 'Credential stored in agent config',
  severity: 'high',
  category: 'secrets',
  description:
    'An MCP server or agent settings file sets a credential-looking environment variable or HTTP header ' +
    '(`*_API_KEY`, `*_TOKEN`, `Authorization`, \u2026) to a literal value, or embeds credentials in a server URL. ' +
    'Complements BK011 for tokens with no recognisable format.',
  remediation:
    'Move the value out of the config file: reference an environment variable (`${GITHUB_TOKEN}`), use the ' +
    "client's input/secret prompt support, or a secret-manager reference.",
  run(ctx, report) {
    for (const s of ctx.servers) {
      const entries: Array<[string, string, 'env' | 'header']> = [
        ...Object.entries(s.env).map(([k, v]) => [k, v, 'env'] as [string, string, 'env']),
        ...Object.entries(s.headers).map(([k, v]) => [k, v, 'header'] as [string, string, 'header']),
      ];
      for (const [key, value, kind] of entries) {
        const sensitive = kind === 'env' ? SENSITIVE_KEY.test(key) : SENSITIVE_HEADER.test(key) || SENSITIVE_KEY.test(key);
        if (!sensitive || !literalCredential(value.replace(/^(Bearer|Basic|token)\s+/i, ''))) continue;
        if (matchesKnownSecret(value) && s.file.rawScan) continue; // BK011 already reports it
        report({
          file: s.file,
          line: lineOf(s.file.content, [`"${key}"`, `${key} =`, `${key}=`], s.line, 0),
          message: `MCP server "${s.name}" stores ${kind === 'env' ? 'environment variable' : 'header'} ${key} as a literal: ${redact(value)}`,
        });
      }
      if (s.url) {
        const userinfo = /^[a-z]+:\/\/[^/\s:@]+:([^/\s@]+)@/i.exec(s.url);
        const query = /[?&](api[_-]?key|apikey|token|access[_-]?token|key|secret|auth)=([^&#\s]{8,})/i.exec(s.url);
        const leaked = userinfo?.[1] ?? query?.[2];
        if (leaked && literalCredential(leaked)) {
          report({
            file: s.file,
            line: s.line,
            message: `MCP server "${s.name}" embeds a credential in its URL: ${redact(leaked)}`,
          });
        }
      }
    }
    for (const file of ctx.files) {
      if (file.kind !== 'config' || !file.data || typeof file.data !== 'object') continue;
      const env = (file.data as Record<string, unknown>).env;
      if (!env || typeof env !== 'object' || Array.isArray(env)) continue;
      for (const [key, value] of Object.entries(env as Record<string, unknown>)) {
        if (typeof value !== 'string' || !SENSITIVE_KEY.test(key) || !literalCredential(value)) continue;
        if (matchesKnownSecret(value) && file.rawScan) continue;
        report({
          file,
          line: lineOf(file.content, [`"${key}"`], 1),
          message: `Settings "env" block stores ${key} as a literal: ${redact(value)}`,
        });
      }
    }
  },
};
