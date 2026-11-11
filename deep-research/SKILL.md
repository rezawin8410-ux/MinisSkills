---
name: deep-research
description: Web search, page reading and multi-source deep research with no API key — twenty-one providers, five-stage fetch fallback with machine-readable rewrites (direct → relays → two vendor readers → Wayback), relevance gating, cross-provider agreement ranking, and cited markdown reports. Use whenever a question needs current facts, verification, comparison of products/APIs, or "look this up and tell me what you find".
version: 1.6.1
license: MIT
---

# deep-research

Three scripts that stack: `search.py` finds, `fetch.py` reads, `research.py`
does the whole loop and writes a cited report.

No key, no account, no SDK. Everything is stdlib `urllib` + `html.parser`.

## The one-liner

```bash
research.py "does vheer allow commercial use of the free image tier" -o vheer.md
```

Output: `vheer.md` (cited report) + `vheer.json` (full trail: every candidate,
provider, error, and raw extract). Ask for `--json` in agent contexts to get a
single machine-readable line back.

## search.py — finding things

```bash
search.py "free tts api"                       # web group: bing + ddg + wikipedia
search.py "edge-tts 403" --group tech          # hn + stackexchange + github
search.py "speech synthesis" --group academic  # arxiv + openalex + europepmc + crossref
search.py "rust async" --all --json
search.py "numpy" --provider pypi              # package lookup
```

How it survives: DuckDuckGo, Bing and everyone else block datacenter IPs
(verified: 202 / 403 / challenge pages). So the two general engines are reached
**through the reader proxy**, which fetches from *its* IP and returns markdown —
one parser then handles every engine's output, unwrapping `duckduckgo.com/l/?uddg=`
and `bing.com/ck/a?...&u=` redirects. Bing's RSS feed is kept only as a last
resort: **it is genuinely broken** and returns unrelated commercial results.

Ranking: term overlap (idf-ish) + cross-provider agreement + title matches +
domain diversity (max 2 per host). If a search returns **zero** results it
automatically relaxes the query to its three most distinctive words and retries.

## fetch.py — reading things

```bash
fetch.py https://example.com --max-chars 8000
fetch.py url1 url2 url3 --out pages/ --json
fetch.py https://example.com --links
```

Three-stage fallback per URL:

1. **direct** — urllib with a browser UA, gzip, redirects, HTML→markdown
   (boilerplate, cookie banners, nav and footers stripped; headings preserved)
2. **reader** — `r.jina.ai/<url>` markdown, then CORS relays, for JS-rendered
   and anti-bot pages
3. **archive** — Wayback Machine snapshot, for pages that are gone

PDFs are handled (pdftotext → pypdf → reader proxy). Everything is cached for
24 h under `~/.cache/agent-skills/fetch/`, so loops don't re-hit the same host.

## research.py — the whole loop

1. expands the question into `depth` query variants
2. searches all of them in parallel
3. merges / dedupes / ranks, max 2 results per domain
4. filters off-topic pages with a relevance gate *before* reading them
5. fetches the survivors in parallel with the five-stage fallback
6. scores sentences by idf-weighted overlap with the question and extracts the
   ones that answer it, deduping at 70 % similarity
7. pulls out every number, unit and date for sanity-checking
8. writes a cited report + JSON sidecar, and lists the pages it could not read

```bash
research.py "question" --depth 1 --max-sources 4      # fast look
research.py "question" --depth 3 --max-sources 12     # broad sweep
research.py "question" --no-fetch --json              # candidates only, no reading
research.py "question" --group academic               # papers only
```

## Rules for using the output

- **The report's sentences are quotations from the sources, not conclusions.**
  Attribute them (`[3]`) and do your own reasoning on top.
- **Never cite a page that appears under "Could not read".** It is listed there
  precisely so you don't.
- **Cross-check numbers.** The "Numbers" table shows how many sources agree on
  each value; a value seen once is a claim, not a fact.
- If the report has fewer than 3 sources, say so in your answer instead of
  presenting a thin result as settled.

## Failure handling

| Symptom | Meaning | Action |
|---|---|---|
| `Provider warnings` in the report | one provider failed (rate limit, block) | fine — others covered it; re-run with `--no-cache` for a fresh mix |
| `reader: HTTP 401/403` | the markdown reader refuses this IP (happened 2026-11-01) | it is auto-disabled for the run and the CORS relays take over |
| `reader: HTTP 429` / `no proxy relayed` | relay rate limits | wait ~1 min, or `--no-fetch` and fetch later |
| `all providers failed` (exit 2) | no network, or everything blocked | check connectivity; fall back to your own knowledge and say it is unverified |
| empty report + `relaxed_from` in the JSON | query too specific | that is the relaxation working — check the sources it found instead |
| `thin content` in errors | page is JS-only | the reader fallback should catch it; if not, the page is genuinely unreadable |

## Notes for the agent

- One `research.py --depth 2` run costs 6–20 seconds and ~10 HTTP calls. Use it
  for questions that matter; use `search.py` alone for quick lookups.
- Cache is your friend: identical queries within an hour are free. `--no-cache`
  when you need freshness (news, prices, "today").
- Prefer `--group academic` for anything that needs a citation trail, `--group
  tech` for library/API errors, `web` for everything else.

## Shared cache

Every skill that caches writes into one root, so you can see and clean it as a
unit instead of hunting through three directories:

| Namespace | Written by | TTL |
|---|---|---|
| `<root>/search/` | `web-search`, `deep-research` | 1 h |
| `<root>/fetch/` | `fetch-page`, `deep-research` | 24 h |
| `<root>/piper/` | `anon-audio` voice files | never pruned |
| `<root>/kv/<ns>/` | anything else (`cachectl put/get`) | caller's choice |

`root = $AGENT_SKILLS_CACHE`, else `~/.cache/agent-skills`.

```bash
python3 scripts/_cachekit.py stats            # what is using the disk
python3 scripts/_cachekit.py prune --days 1   # drop entries older than a day
python3 scripts/_cachekit.py clear fetch      # wipe one namespace
python3 scripts/_cachekit.py clear all        # everything except voice models
AGENT_SKILLS_CACHE=/tmp/c python3 scripts/search.py "q"   # throwaway cache
```

Model files are protected from `clear all` and `prune` on purpose — they are the
one thing here that costs real time to get back.
