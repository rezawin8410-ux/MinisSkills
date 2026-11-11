---
name: fetch-page
description: Turn any URL into clean markdown for an agent, with a five-stage fallback plus machine-readable rewrites (direct → two independent kinds of relay → two vendor readers that render JS and get past bot walls → Wayback Machine), and a PDF extractor that needs no dependency, a hard wall-clock deadline, PDF support and a 24-hour cache. Use whenever you have a URL and need its readable content — documentation, articles, changelogs, terms pages, papers, or a page a plain request cannot get through.
version: 1.8.0
license: MIT
---

# fetch-page

Give it URLs, get markdown. Five transports per URL, in order, so a blocked or
JS-heavy page is not a dead end — and a hard deadline, so a hostile page cannot
hang the agent that asked.

## Use this when

- you have a specific URL and need what it *says*
- a previous `curl` returned a cookie wall, a JS shell, or a 403
- you need a PDF as text
- you want several pages read in parallel and written to disk

Use `web-search` when you don't have a URL yet.

## Quick start

```bash
fetch.py https://example.com
fetch.py https://vheer.com/terms --max-chars 8000
fetch.py url1 url2 url3 --out pages/ --json
fetch.py https://example.com --links            # also extract the links
fetch.py https://example.com --text             # print the text, no files
fetch.py https://example.com --deadline 15      # give up after 15s, no matter what
fetch.py https://example.com --no-vendor        # five stages -> three (no vendor calls)
fetch.py https://example.com --no-rewrite       # skip the machine-readable twins
fetch.py https://www.reddit.com/r/pics/comments/1xm0xm3/ --media pics/   # post + comments + images
fetch.py https://x.com/Complex/status/2101466653175464354 --media x/     # tweet + photos/video
```

## The fallback chain

| Stage | Transport | Handles |
|---|---|---|
| 0. rewrite | reddit → arctic-shift API; x/twitter → fxtwitter API; github blob → raw; StackOverflow → `api.stackexchange.com` | the hosts where the page is a UI or a wall around the content. Runs **before** direct |
| 1. direct | urllib, browser UA, gzip, redirects | normal pages; a hand-written `html.parser` extractor drops nav/footer/aside/forms and cookie/subscribe boilerplate, keeps headings and list structure |
| 2. reader | `r.jina.ai/<url>` markdown, then the CORS relays (`allorigins` → `cors.lol` → `codetabs`) | JS-rendered pages, anti-bot walls, PDFs when no local extractor exists |
| 3. vendor:firecrawl | `api.firecrawl.dev/v2/scrape`, no credential | the hard walls: JS shells, Cloudflare, 403-to-plain-HTTP pages |
| 4. vendor:exa | `mcp.exa.ai/mcp` JSON-RPC `web_fetch_exa`, no credential | the same walls when Firecrawl is down or rate-limited; returns the page's own markdown |
| 5. archive | Wayback Machine snapshot | pages that are gone, moved or blocked entirely |

**Machine-readable twins.** Some URLs have a second form that is one request
and no navigation: a github blob is the same file with a UI wrapped around it,
a StackOverflow question is the same Q&A with Cloudflare in front of it. Those
two run *before* the direct stage, because direct does not fail on them — it
returns 200 with a navigation sandwich, which is worse. The rest run after the
relays:

| URL | twin | measured 2026-11-02 |
|---|---|---|
| `github.com/…/blob/…` | `raw.githubusercontent.com/…` | 200, clean README, 0.1 s |
| `stackoverflow.com/questions/<id>`, `*.stackexchange.com`, superuser, serverfault, askubuntu | `api.stackexchange.com/2.3/questions/<id>?filter=withbody` | 200, rendered as question + accepted answer + top answers |
| `en.wikipedia.org/wiki/X` | `…/api/rest_v1/page/summary/X` | 200, the lede, labelled as a summary |
| `x.com/…`, `twitter.com/…` | `nitter.net`, `nitter.privacydev.net`, `xcancel.com` | nitter answers; xcancel 451 |
| `medium.com/…` | `scribe.rip/…` | server alive, unverified on a real post |
| `youtube.com/watch?v=…`, `youtu.be/…` | `youtube.com/oembed?…` | 200, title + channel, not the video |
| `arxiv.org/abs/…` | `arxiv.org/pdf/…` | 200, the paper itself |

`--no-rewrite` turns the whole row off. Rewrites are keyless direct endpoints,
so they are not affected by `--no-vendor`.

**Reddit and X, with the pictures.** Both are closed to plain clients — reddit.com
403s every datacenter IP (and `old.reddit`, `.json` and `/s/` share links with it),
while x.com wants a session and its API wants a bearer token. Neither needs one:

| you give it | it calls | you get |
|---|---|---|
| any reddit post (`/r/*/comments/<id>`, `redd.it/<id>`, `/gallery/<id>`) | `arctic-shift.photon-reddit.com/api/posts/ids?ids=<id>` + `/api/comments/search?link_id=<id>` | title, subreddit, score, selftext, **every image** (galleries in order, with dimensions), top 20 comments by score |
| a subreddit or user page | `/api/posts/search?subreddit=…` (or `author=…`) | 25 newest posts with links and their images |
| any x/twitter status | `api.fxtwitter.com/status/<id>` | author, date, text, **all photos at `name=orig`**, videos/gifs with duration, quoted tweet, counts |

`--media DIR` downloads what it found: photos, and for reddit/X video posts the
mp4 too, numbered so they stay in gallery order. `media` in the JSON lists every
item before you download anything. Images come from `i.redd.it`, `preview.redd.it`
(signed URLs straight out of the gallery metadata) and `pbs.twimg.com` /
`video.twimg.com`, all of which answer plain HTTP without cookies.

Measured 2026-11-02: reddit image post 2.0 s, gallery of 2 → both JPEGs saved and
verified with `file`; subreddit listing 25 posts in 4 s; x post with three photos
0.3 s, 3/3 saved; x video post returns the 1280×720 mp4 (65 s).

What does **not** work here, and is therefore not in the chain: `old.reddit.com`
and every `.json` endpoint (403), reddit `/s/` share links (403 — pass the
canonical `/comments/<id>/` URL instead), redlib mirrors (429 / Cloudflare on all
eight tried), `api.pullpush.io` (522 today, wired as a "maybe" not a path),
nitter (Cloudflare), `api.vxtwitter.com` (Cloudflare) and the syndication
endpoint (returns `{}` without a computed token).

**Walls are not answers.** A relay that returns 200 with a bot challenge or a
signup gate has not read the article. Every accepted body is checked before it
is cached: challenge markers (`just a moment`, `checking your browser`, a
relay's `Warning: Target URL returned error 403`) and signup gates (`sign up
with google`, `log in to continue`, `subscribe to continue`) reject the stage
and the chain moves on. Without this, `fetch.py
https://stackoverflow.com/questions/1976007` returned `ok: true` with 20,000
characters of StackOverflow's own signup page — inside the length cap, past the
thin-content check, and quotable by an agent that had read nothing.

**Why two vendor readers, and why Exa sits before Wayback.** Firecrawl is the
better scraper when it works; it is also a single borrowed endpoint that can be
rate-limited or down. Exa's MCP reader covers that gap with the page's own
markdown instead of an archived snapshot — when both answer, the live page beats
whatever copy the archive happened to crawl. Both are gated by the same
`--no-vendor` / `FETCH_VENDOR=0` switch and both get exactly one attempt per URL,
because knocking twice on a free endpoint is how you get blocked.

Measured on 2026-11-02 with Firecrawl deliberately pointed at a dead port:
`fetch.py https://www.g2.com/products/ffmpeg/reviews --deadline 30` returned
`vendor:exa`, 13,746 chars in 16 s — the direct stage 403s, the reader hangs, and
the page still came back.

**Exa's `maxCharacters` gotcha:** left unset, `web_fetch_exa` returns ~3,000
characters and looks terminally truncated. This skill asks for 50,000, which
returned 31,442 on a long Wikipedia article. If you ever see `vendor:exa` results
that stop mid-sentence around 3k, something unset the parameter.

PDF text: `pdftotext` → `pypdf` → **built-in parser** → reader proxy. The
built-in one is pure stdlib — it inflates the content streams itself and decodes
TeX ligature glyphs and subset-font encodings — which matters on exactly the
machines this pack targets. Measured with neither poppler nor pypdf installed:
`arxiv.org/pdf/1706.03762` came through at 20,000 chars (the cap) in 0.6 s.
If all four fail you get a clear error naming what was missing, not an empty
file.

## Output

- default: a one-line human summary per URL, `[ok] 11860 chars · direct · 0.3s · Title`
- `--json`: `[{url, ok, title, chars, source, elapsed, final_url, errors[], file?}]`
- `--out dir/`: writes one `.md` per page with a YAML header (url, title, source,
  chars, fetched_at) plus a `## Links` section when `--links` is set

`source` tells you which stage won — `direct`, `reader:jina`, `reader:allorigins`,
`reader:cors.lol`, `vendor:firecrawl`, `vendor:exa`, `reddit:post`,
`reddit:listing`, `x:post`, or `wayback`. That matters: a Wayback result is a *snapshot*,
so check `snapshot_ts` before quoting it as current.

**Why two kinds of relay:** on 2026-11-01 `r.jina.ai` began answering
`401 You have been blocked` to this network, which broke every URL that a plain
request could not reach. The CORS relays were added that day; the markdown reader
is still tried first because its output is cleaner, and it disables itself for the
run as soon as it refuses us.

## Failure modes

| Symptom | Meaning | Action |
|---|---|---|
| `thin content (180 chars) — probably JS-rendered` | stage 1 hit a shell page | it already fell through to the reader; if the final `source` is still `direct`, the page is genuinely empty |
| `reader: HTTP 401/403` | the markdown reader refuses this IP | expected since 2026-11-01; the CORS relays take over automatically |
| `no proxy relayed: … 429/522/timeout` | every relay is rate-limiting at once | wait ~1 min, or `--no-archive --no-reader` if the page is static |
| `reader:allorigins HTTP 500` | that relay choked on this URL | the next relay is already being tried |
| `no Wayback snapshot` | page never archived | the URL is probably wrong; re-check it |
| `refusing scheme file: — only http and https are fetched` | you passed `file://`, `ftp://`, `data:` … | deliberate: this tool must never be a way to read local files. Read them directly instead |
| `must be 1 or more (got 0)` | an impossible flag value | none of these flags accept 0 or negatives; the message says which |
| `PDF but no text extractor` | no pdftotext/pypdf | `apt install poppler-utils` or `pip install pypdf`; the reader proxy usually covers you anyway |
| empty text, every stage failed | paywall or hard auth wall | report it, do not invent content |

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

## Caching

`~/.cache/agent-skills/fetch/`, 24 h TTL, keyed by URL. `--no-cache` bypasses and
rewrites; `--ttl 0` disables. A research loop that revisits the same host is free
after the first pass.

## Notes for the agent

- **Quote from the returned text, never from the snippet you saw in search.**
  Snippets are truncated and occasionally wrong.
- Check `chars` before trusting a source: under ~500 characters usually means you
  got a stub, not an article.
- `--max-chars` defaults to 20000, which is roughly a long article. Raise it for
  documentation pages, lower it when you only need the top of a page.
- `--json` is machine output and stays parseable: with `--text` the text goes
  *inside* the JSON, never after it.
- `--out` writes quoted frontmatter (`title: "Python: The Basics"`), so a page
  title with a colon, a newline or a `|` cannot break the YAML block or the link
  list, and `user:pw@` in a URL is redacted before it reaches the file.
- `chars` is what you got, `full_chars` is what the page holds. They differ when
  `--max-chars` cut the text; re-run with a bigger cap to read the rest. The cache
  stores the whole page, so asking for more is a cache hit, not a refetch.
- Respect the hosts you hit: 4 parallel fetches is the default and is plenty.

## The four stages

| # | stage | what it is | why |
|---|---|---|---|
| 1 | `direct` | urllib with a browser UA, gzip, redirects, HTML→markdown | free, fast, no third party |
| 2 | `reader` | `r.jina.ai` markdown → CORS-proxied HTML (allorigins, cors.lol, codetabs) | JS shells, soft paywalls |
| 3 | `vendor` | Firecrawl's public `/v2/scrape`, no credential | renders JS, survives Cloudflare |
| 4 | `archive` | Wayback Machine snapshot | pages that are gone or blocked forever |

Stage 3 exists because it fixes real failures, not theoretical ones. Measured on
2026-11-02 against this exact chain: `g2.com` (Cloudflare) and `medium.com` hung
the reader stage indefinitely and returned nothing — the vendor stage returned
6,384 and 4,760 characters in about two seconds. `x.com` went from 1,669 to
36,318 characters. It is third, not first, because it is borrowed infrastructure
with no SLA and the direct path costs the vendor nothing. Disable it with
`--no-vendor` or `FETCH_VENDOR=0`.

## The deadline

`--deadline` (default 45 s) is a hard wall-clock cap per URL, enforced from
*outside* each stage: a stage that ignores its own socket timeouts is abandoned
in a daemon thread and the next stage gets its turn. Each stage also receives a
share of the deadline (direct 35%, reader 40%, vendor 50%, exa 45%, archive 30%), so a
hanging relay cannot starve the stage behind it.

Before this existed, `fetch.py https://www.g2.com/…` never returned. It now
returns in 16 s with content — never by hanging.

Two fields report what happened, and they mean different things:

| field | meaning |
|---|---|
| `deadline_exceeded` | the *call* was cut short and returned `ok: false` |
| `stages_cut` | stages abandoned along the way, on success too |

A real g2.com run under `--deadline 25` gives `ok: true`, `source:
vendor:firecrawl`, `deadline_exceeded: false`, `stages_cut: ["reader"]` — the
reader hung, the vendor stage answered anyway, and nothing was lost. Read
`stages_cut` when you are debugging latency; read `deadline_exceeded` when you
are deciding whether to retry.
