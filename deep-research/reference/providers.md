# deep-research — provider status (verified 2026-11-02)

## Working without a key

| Provider | Groups | Transport | Notes |
|---|---|---|---|
| `bing` | web | Bing SERP via transport chain | primary general index; unwraps `ck/a?u=a1…` redirects |
| `ddg` | web | DDG html via transport chain | second general index; unwraps `l/?uddg=` |
| `ddg_lite` | spare | DDG lite surface, same chain | not in any group — `--provider ddg_lite` when `ddg` is down |
| `wikipedia` | web | MediaWiki API (JSON) | entity lookups only — skipped automatically for queries > 6 words |
| `bingnews` | web | Bing News RSS | real article URLs (`?url=` param) |
| `gnews` | web | Google News RSS | headline + publisher + date only; URLs are opaque tokens |
| `hn` | tech | Algolia API | stories, points, comments |
| `stackexchange` | tech | StackExchange API | best source for concrete programming errors |
| `github` | tech | GitHub REST | repositories by stars |
| `npm` | tech | registry search | packages |
| `pypi` | tech | `pypi/<name>/json` | **lookup, not search** — excluded from `all` |
| `arxiv` | academic | arXiv Atom API | preprints |
| `openalex` | academic | OpenAlex API | citation counts, open metadata |
| `europepmc` | academic | Europe PMC | biomedical + abstracts |
| `crossref` | academic | Crossref API | DOIs |
| `ddg_ia` | web | DuckDuckGo Instant Answers | definitions, disambiguation |
| `bing_rss` | web | Bing RSS — no relay needed | noisy on some queries, clean on others; kept in the `web` group as the relay-free backstop. The off-topic gate discards the junk. |

## Blocked from a datacenter IP (tested, do not retry without a relay)

| Endpoint | Result |
|---|---|
| `html.duckduckgo.com` / `lite.duckduckgo.com` direct | `202` anomaly/challenge page on most days; sometimes `200` — hence "direct" is tried first, cheaply |
| `www.bing.com/search` direct (HTML) | challenge |
| `search.brave.com` | blocked page |
| `www.mojeek.com` (html + rss) | `403` |
| `www.ecosia.org` | `403` |
| `search.yahoo.com` | connection killed |
| `searx.be`, `baresearch.org`, `priv.au`, `searx.tiekoetter.com` | HTML instead of JSON, or `429` |
| `reddit.com/search.json` | `403` |
| `api.semanticscholar.org` | `429` |
| `search.marginalia.nu` | error page |

**The trick that makes this skill work:** reach the blocked engines through a
relay that fetches from *its* IP. Three, tried in order, all feeding one parser:

| Order | Relay | Format | State |
|---|---|---|---|
| 1 | direct (no relay) | HTML | free when the IP is not blocked |
| 2 | `r.jina.ai/<url>` | markdown | **401-blocked this network since 2026-11-01** — auto-disabled at runtime |
| 3 | `api.allorigins.win/raw?url=` | HTML | works, but 502/522 in bursts |
| 4 | `api.cors.lol/?url=` | HTML | works, 429s when hammered |
| 5 | `api.codetabs.com/v1/proxy?quest=` | HTML | 522 more often than not |

Dead relays found this round: `corsproxy.io` (401), `whateverorigin.org`
(400 — demands an `Origin` header), `api.marginalia.nu` (timeout), `mojeek` and
`startpage` through allorigins (408/522).

## fetch.py fallback chain

1. **direct** — urllib, browser UA, gzip, redirects, `html.parser`-based
   extraction (drops script/style/nav/footer/aside/form, strips cookie and
   subscription boilerplate, keeps headings and list structure)
2. **reader** — `r.jina.ai/<url>` (markdown) first; if that relay is dead or
   refuses you, the same page is pulled through the CORS relays and reduced with
   the local HTML reader — JS-rendered pages, anti-bot walls, and PDFs when no
   local extractor exists
3. **archive** — `archive.org/wayback/available` → snapshot → re-extract

PDF: `pdftotext` (poppler) → `pypdf` → reader proxy.

## Caching

| Store | Path | TTL |
|---|---|---|
| search results | `~/.cache/agent-skills/search/` | 1 h |
| fetched pages | `~/.cache/agent-skills/fetch/` | 24 h |

`--no-cache` on either tool bypasses and rewrites.

## Vendor tier (opt-in, no key, no SLA)

Not in `--all` and not in any default group. Add with `--group vendor` or `--with-vendor`.

| Provider | Transport | Measured 2026-11-02 | Notes |
|---|---|---|---|
| `keenable` | `POST api.keenable.ai/v1/search/public` | 0.2 s, excerpts capped at 1,200 chars | sends `X-Keenable-Title: keyless-agent-skills` (an app id is required; ours, not a borrowed one); **search only** — `/v1/extract`, `/v1/read`, `/v1/scrape`, `/v1/reader` all 404 |
| `firecrawl` | `POST api.firecrawl.dev/v2/search`, `POST api.firecrawl.dev/v2/scrape` | 0.4 s, 400–700 char snippets, best domain diversity | no `Authorization` header at all; the only vendor with both search and extract |
| `exa` | `POST mcp.exa.ai/mcp` JSON-RPC `web_search_exa` + `web_fetch_exa` | search 0.5 s, ~2,700 char excerpts; fetch 0.3 s, up to ~31 k chars | search **and** extract. `web_fetch_exa` returns ~3,000 chars unless `maxCharacters` is passed — that default is not a hard cap |

Why opt-in: these are undocumented free tiers. They answered 5/5 queries on the day
our classic general providers were blocked from this host, but they can require a key
or disappear without notice. Never make one the only path.

## Classic tier: Yahoo

`yahoo` joined the `web` group on 2026-11-02. Bing's index behind a front end
that still answers non-browser clients: 3/3 queries, ~0.8 s, relevant — while
from the same host `bing` served junk, `ddg` 403'd, `google` hit a consent wall,
and brave/mojeek/startpage were 429/403/empty. It needs one thing plain
`urlopen` does not do: keep the cookie from its first (500) response, or every
retry 500s forever. `search.py` now runs one cookie jar for the whole process.
