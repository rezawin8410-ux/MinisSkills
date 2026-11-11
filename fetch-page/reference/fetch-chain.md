# fetch-page — transport details

The chain as it runs today, in order. Stages 0 and 1 are tried before anything
third-party is involved; `--no-rewrite` and `--no-reader` remove the first and
the relays; `--no-vendor` removes Firecrawl and Exa; `--no-archive` removes
Wayback. Every stage shares one hard wall-clock deadline.

## Stage 0: machine-readable rewrites (before direct)

Some URLs have a second form that is one request, no navigation and no bot wall.
Two of those run *before* the direct stage, because direct does not fail on them
— it returns 200 with a navigation sandwich around the content, which is worse
than failing. The rest run after the relays have had their turn.

| Runs | URL | Twin |
|---|---|---|
| before | `reddit.com/r/*/comments/<id>`, `redd.it/<id>`, `/gallery/<id>` | `arctic-shift.photon-reddit.com/api/posts/ids` + `/api/comments/search` |
| before | `reddit.com/r/<sub>`, `/user/<name>` | `arctic-shift.photon-reddit.com/api/posts/search` |
| before | `x.com`/`twitter.com/…/status/<id>` | `api.fxtwitter.com/status/<id>` |
| before | `github.com/…/blob/…` | `raw.githubusercontent.com/…` |
| before | `stackoverflow.com/questions/<id>`, `*.stackexchange.com`, superuser, serverfault, askubuntu | `api.stackexchange.com/2.3/questions/<id>?filter=withbody` |
| after relays | `en.wikipedia.org/wiki/X` | `…/api/rest_v1/page/summary/X` (the lede, labelled as a summary) |
| after relays | `x.com/…`, `twitter.com/…` | `nitter.net`, `nitter.privacydev.net`, `xcancel.com` — first that answers |
| after relays | `medium.com/…` | `scribe.rip/…` |
| after relays | `youtube.com/watch?v=…`, `youtu.be/…` | `youtube.com/oembed?…` (title + channel, not the video) |
| after relays | `arxiv.org/abs/…` | `arxiv.org/pdf/…` |

StackExchange JSON is *rendered*, not dumped: question title, score, answer
count, the body, then up to three answers with the accepted one marked. Measured
2026-11-02: the StackOverflow URL above returns a rendered Q&A in 0.8 s where
every other stage returned a Cloudflare 403 or a signup wall.

Not included, because they measured dead on this network: reddit → `old.reddit`
/ `.json` (403 both) and `archive.ph` (429).

### Reddit and X in detail

Both hosts are closed to anonymous clients and both have a keyless JSON service
that is not. The stages live in `fetch.py` as `fetch_reddit()` and `fetch_x()`,
not as single-URL rewrites, because each needs more than one request.

**Reddit.** `arctic-shift.photon-reddit.com` returns the full post object —
`title`, `selftext`, `score`, `created_utc`, `is_gallery`, `gallery_data`,
`media_metadata`, `preview` — plus comments via `/api/comments/search?link_id=`.
Media collection order: `media_metadata` in `gallery_data` order (this is what
carries the signed `preview.redd.it` URLs), then `url_overridden_by_dest` when it
is an image host, then `reddit_video.fallback_url`, then `preview.images[0]`.
Duplicates collapse on the URL without its query string. A post with no media is
still a post; a post the archive does not have raises and the chain moves on.

Listings use `/api/posts/search?subreddit=<sub>&limit=25&sort=desc` (or
`author=` for user pages); `sort=asc` maps to `/new`, `after`/`before` exist for
paging. There is no global full-text search: `query=`, `title=` and `selftext=`
all require a subreddit or author scope, which is why no reddit provider was added
to `web-search`.

Measured: post 0.3–2.0 s, listing (25 posts + images) ~4 s, images 130–860 kB
straight off `i.redd.it`.

**X.** `api.fxtwitter.com/status/<id>` — id only, no handle needed, ids as short
as `20` work. Media arrays give `photos` (pbs.twimg.com, `?name=orig` = full
size), `videos` and `gifs` (video.twimg.com mp4, with duration), plus `quote`.
`video.twimg.com` honours range requests, so partial downloads work.

Dead on this network on 2026-11-02, do not retry as a strategy: `old.reddit.com`,
`*.reddit.com/*.json`, reddit `/s/` share links (all 403); eight redlib mirrors
(429 / 503 / Cloudflare / redirect loops); `api.pullpush.io` (522); nitter.net and
the other nitter mirrors (Cloudflare challenge with a 200 status — which is why
the wall check exists); `api.vxtwitter.com` (Cloudflare); the syndication
endpoint (`{}` without a token); `publish.twitter.com/oembed` (301).
`i.imgur.com` now 302s to `removed.png` for most ids, so imgur is followed, not
trusted.

`source` values: `reddit:post`, `reddit:listing`, `x:post`,
`rewrite:gh-raw`, `rewrite:stackexchange`, `rewrite:wikipedia-summary`,
`rewrite:nitter`, `rewrite:nitter-privacydev`, `rewrite:xcancel`, `rewrite:scribe`,
`rewrite:youtube-oembed`, `rewrite:arxiv-pdf`.

## Stage 1: direct

- Headers sent: browser `User-Agent`, `Accept: text/html,application/xhtml+xml,application/pdf,*/*`,
  `Accept-Language`, `Accept-Encoding: gzip, identity`
- Transparent gzip decompression (also sniffs the `1f 8b` magic bytes, because
  some hosts gzip without saying so)
- Redirects followed; `final_url` records where you landed
- Content-type routing: `pdf` → PDF extractor; `json` → pretty-printed text;
  everything else → HTML reader
- The reader is a `html.parser` subclass: it skips
  `script/style/noscript/svg/iframe/template/head/nav/footer/aside/form/button/select/figure`,
  emits block boundaries for `p/div/br/li/tr/section/article/blockquote/pre/table`,
  prefixes headings with `#` and list items with `-`, collects `og:`/`meta`
  metadata, and collapses 3+ blank lines
- Boilerplate filter drops lines matching cookie/consent/subscribe/newsletter/
  sign-in/all-rights-reserved/©/privacy/terms/advertisement patterns, plus
  consecutive duplicate lines
- Rejects a page under 200 characters as "thin" and falls through

## Stage 2: relays (two kinds, four endpoints)

The reader is a third party. It is the part of this chain most likely to stop
working, so there are two independent ways of getting a page from someone else's
IP, tried in order:

| Order | Endpoint | Returns | Notes |
|---|---|---|---|
| 2a | `r.jina.ai/<url>` | markdown (`Title:`, `URL Source:`, `Markdown Content:`) | renders JS with a real browser; free tier ≈ 20 req/min. Answered `401 You have been blocked` from this network on 2026-11-01, and 200 again on 2026-11-02 — the code disables it for the run on 401/403/429 but never removes it permanently |
| 2b | `api.allorigins.win/raw?url=` | raw HTML → local extractor | volunteer relay; 502/522 in bursts |
| 2c | `api.cors.lol/?url=` | raw HTML → local extractor | volunteer relay; 429 when hammered |
| 2d | `api.codetabs.com/v1/proxy?quest=` | raw HTML → local extractor | mostly 522; kept as a last resort |

Dead ends, tested and dropped: `corsproxy.io` (`401`), `whateverorigin.org`
(`400` — demands an `Origin` header), `api.marginalia.nu` (timeout).
`source` in the JSON tells you which one won: `reader:jina`, `reader:allorigins`,
`reader:cors.lol`, `reader:codetabs`.

If *all* relays fail, the error string lists what each one said
(`no proxy relayed: …`) — that is a diagnostic, not a crash.

## Stages 3 and 4: vendor readers (opt-in by default-on, killable)

Both are borrowed free endpoints with no SLA. They get exactly one attempt per
URL — knocking twice on someone's undocumented endpoint is how you get blocked.

| Stage | Endpoint | What it does |
|---|---|---|
| 3. `vendor:firecrawl` | `POST api.firecrawl.dev/v2/scrape`, no `Authorization` header | renders JS, gets past Cloudflare; the stage that reaches g2.com-class pages |
| 4. `vendor:exa` | `POST mcp.exa.ai/mcp`, JSON-RPC `web_fetch_exa` | covers Firecrawl being down or rate-limited; returns the page's own markdown |

Exa's `maxCharacters` matters: unset, it returns ~3,000 characters and looks
terminally truncated. This skill asks for 50,000, which returned 31,442 on a long
Wikipedia article.

## Stage 5: archive

`https://archive.org/wayback/available?url=<url>` → `archived_snapshots.closest.url`
→ re-run stage 1 against the snapshot, then relabel `source: wayback` and record
`snapshot_ts`. Always check the timestamp before presenting an archived page as
current information.

## The wall check, applied to every stage

A response is not accepted just because it is long. Before a body is cached it is
checked for:

- **challenge walls** — `just a moment`, `checking your browser`, `unusual
  traffic`, `cf-error-details`, and the relays' own `Warning: Target URL returned
  error 403` wrapper
- **signup gates** — `sign up with google`, `sign up with github`, `join stack
  overflow`, `log in to continue`, `subscribe to continue`, `for subscribers
  only`, `become a member to read`

A hit rejects the stage (`wall page, not content (…)` in `errors`) and the chain
moves on. Before this existed, `fetch.py https://stackoverflow.com/questions/1976007`
returned `ok: true` with 20,000 characters of StackOverflow's signup page: inside
the length cap, past the thin-content check, and quotable by an agent that had
read nothing.

## The deadline

`--deadline` (default 45 s) is a hard wall-clock cap per URL, enforced from
*outside* each stage: a stage that ignores its own socket timeouts is abandoned in
a daemon thread and the next stage gets its turn. Each stage also receives a share
of the deadline — priority rewrites 35%, direct 35%, reader 40%, fallback rewrites
30%, Firecrawl 50%, Exa 45%, archive 30% — so a hanging relay cannot starve the
stage behind it.

Two fields report what happened, and they mean different things:

| field | meaning |
|---|---|
| `deadline_exceeded` | the *call* was cut short and returned `ok: false` |
| `stages_cut` | stages abandoned along the way, on success too |

## PDF text, in order

1. `pdftotext -layout` (poppler), if installed
2. `pypdf`, if installed
3. **built-in stdlib parser** — inflates the content streams itself, reads the
   text operators, decodes TeX ligature glyphs (`\x0e` → `ffi`) and the
   `/Differences` maps that subset fonts use for the control range
4. a reader relay, as a last resort

Step 3 is what makes PDFs work on a bare Termux or a slim container. Verified with
neither poppler nor pypdf installed: `arxiv.org/pdf/1706.03762` → 20,000 chars (the
cap) in 0.6 s; a two-column paper → 62,884 chars in 0.14 s. It does not attempt
CID/Identity-H fonts — those fall through to the relay.

## What is deliberately *not* here

- **No headless browser.** Playwright costs ~400 MB, needs system libraries, and
  dies on turn boundaries in most agent sandboxes. The vendor readers cover the
  same ground for a fraction of the cost. (`anon-image` keeps an optional
  Playwright path for Vheer's UI; that is the only place it appears.)
- **No login flow.** If a page needs an account, this skill says so — via the wall
  check — instead of returning the signup page as if it were the article.
- **No robots.txt crawling.** It fetches the URLs you name, one at a time, and
  never follows links by itself. (`deep-research` follows links, and says so.)
