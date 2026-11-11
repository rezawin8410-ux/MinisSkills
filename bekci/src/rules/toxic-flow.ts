import type { Agent, McpServer, Rule } from '../types.js';
import { AGENT_LABELS } from '../types.js';

export type Leg = 'private' | 'untrusted' | 'exfil';

/**
 * Capability heuristics, matched against the server name, command, args and URL.
 * `private`  : reads data the user would not want to leak.
 * `untrusted`: puts attacker-controllable text into the context (web, issues, email).
 * `exfil`    : can send data somewhere an attacker can read it.
 */
const CAPABILITIES: Array<[RegExp, Leg[]]> = [
  [/\b(github|gitlab|bitbucket|gitea)\b/, ['private', 'untrusted', 'exfil']],
  [/\b(gmail|outlook|email|imap|smtp|mail)\b/, ['private', 'untrusted', 'exfil']],
  [/\b(slack|discord|teams|telegram|whatsapp|mattermost|zulip)\b/, ['private', 'untrusted', 'exfil']],
  [/\b(jira|linear|asana|trello|clickup|zendesk|intercom|atlassian|confluence)\b/, ['private', 'untrusted', 'exfil']],
  [/\b(notion|gdrive|google-drive|drive|dropbox|onedrive|sharepoint|box|obsidian|evernote)\b/, ['private', 'untrusted']],
  [/\b(filesystem|file-system|desktop-commander)\b/, ['private']],
  [
    /\b(postgres|postgresql|mysql|sqlite|supabase|mongo|mongodb|redis|neon|database|snowflake|bigquery|clickhouse|dynamodb|firestore|prisma)\b/,
    ['private'],
  ],
  [/\b(1password|bitwarden|vault|keychain|secrets?)\b/, ['private']],
  [/\b(stripe|shopify|salesforce|hubspot|quickbooks|xero|sentry|datadog)\b/, ['private']],
  [/\b(shell|terminal|ssh|desktop-commander|computer-use|applescript|iterm)\b/, ['private', 'exfil']],
  [
    /\b(fetch|browser|puppeteer|playwright|chrome|chromium|firecrawl|brightdata|scrape|scraper|crawl|crawler|browserbase|web)\b/,
    ['untrusted', 'exfil'],
  ],
  [/\b(search|brave|tavily|exa|perplexity|serp|serper|kagi|duckduckgo|bing)\b/, ['untrusted']],
  [/\b(twitter|x-mcp|reddit|youtube|hackernews|rss|news|arxiv)\b/, ['untrusted']],
  [/\b(webhook|http-request|requests|curl|api-gateway|zapier|n8n)\b/, ['exfil']],
];

export function capabilities(server: McpServer): Set<Leg> {
  const haystack = [server.name, server.command ?? '', ...server.args, server.url ?? '']
    .join(' ')
    .toLowerCase()
    .replace(/[@/_.:]/g, ' ');
  const legs = new Set<Leg>();
  for (const [re, l] of CAPABILITIES) if (re.test(haystack)) l.forEach((x) => legs.add(x));
  return legs;
}

const LEG_LABEL: Record<Leg, string> = {
  private: 'private data',
  untrusted: 'untrusted content',
  exfil: 'external communication',
};

export const lethalTrifecta: Rule = {
  id: 'BK301',
  name: 'lethal-trifecta',
  title: 'Lethal trifecta: private data + untrusted content + exfiltration',
  severity: 'high',
  category: 'toxic-flow',
  description:
    'Taken together, the MCP servers enabled for one agent can (1) read private data, (2) ingest content an ' +
    'attacker controls, such as web pages, issues or email, and (3) send data out. That combination is all a ' +
    'prompt injection needs to steal data; no individual server has to be malicious. Coined by Simon Willison; ' +
    'demonstrated against the GitHub MCP server, where a public issue made the agent leak private repositories.',
  remediation:
    'Break one leg: do not combine these servers in the same agent profile, scope tokens to public data only, ' +
    'or keep confirmations on for tools that send data out. Use separate profiles for browsing and for private work.',
  references: [
    'https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/',
    'https://invariantlabs.ai/blog/mcp-github-vulnerability',
  ],
  run(ctx, report) {
    const byAgent = new Map<Agent, { global: McpServer[]; projects: Map<string, McpServer[]> }>();
    for (const s of ctx.servers) {
      if (s.disabled) continue;
      const entry = byAgent.get(s.agent) ?? { global: [] as McpServer[], projects: new Map<string, McpServer[]>() };
      byAgent.set(s.agent, entry);
      const projectKey = s.project ?? (s.scope === 'project' ? s.file.path.replace(/[/\\][^/\\]+$/, '') : undefined);
      if (!projectKey) entry.global.push(s);
      else entry.projects.set(projectKey, [...(entry.projects.get(projectKey) ?? []), s]);
    }

    for (const [agent, { global, projects }] of byAgent) {
      const reported = new Set<string>();
      const sets: Array<{ servers: McpServer[]; where?: string }> = [{ servers: global }];
      for (const [p, list] of projects) sets.push({ servers: [...global, ...list], where: p });

      for (const { servers, where } of sets) {
        const providers: Record<Leg, McpServer[]> = { private: [], untrusted: [], exfil: [] };
        for (const s of servers) for (const leg of capabilities(s)) providers[leg].push(s);
        if (!providers.private.length || !providers.untrusted.length || !providers.exfil.length) continue;

        const key = (['private', 'untrusted', 'exfil'] as Leg[])
          .map((l) =>
            providers[l]
              .map((s) => s.name)
              .sort()
              .join(','),
          )
          .join('|');
        if (reported.has(key)) continue;
        reported.add(key);

        const anchor = providers.untrusted[0]!;
        const legs = (['private', 'untrusted', 'exfil'] as Leg[])
          .map((l) => `${LEG_LABEL[l]} via ${[...new Set(providers[l].map((s) => s.name))].join(', ')}`)
          .join('; ');
        const scope = where && servers.some((s) => s.project) ? ` (in ${shorten(where)})` : '';
        report({
          file: anchor.file,
          line: anchor.line,
          message: `${AGENT_LABELS[agent]}${scope}: ${legs}`,
        });
      }
    }
  },
};

function shorten(p: string): string {
  const home = process.env.HOME ?? process.env.USERPROFILE;
  return home && p.startsWith(home) ? '~' + p.slice(home.length) : p;
}
