#!/usr/bin/env python3
"""
search.py — web search for agents with no API key, no account and no vendor lock-in.

Twenty providers, five groups plus an opt-in vendor tier, automatic fallback. Every provider here was
verified to work from a bare `curl`/urllib session; ones that need auth or that
started blocking crawlers (DuckDuckGo HTML, Brave, Mojeek, Ecosia, Reddit,
Semantic Scholar) are deliberately absent.

  search.py "free tts api"                    # web group, human output
  search.py "free tts api" --json             # machine output (agent default)
  search.py "edge-tts 403" --group tech
  search.py "speech synthesis" --group academic --limit 5
  search.py "rust async" --all --json         # every provider, merged + ranked
  search.py "numpy" --provider pypi           # package lookup
  search.py "rust async" --group vendor       # vendor APIs: keyless, no SLA
  search.py "rust async" --web --with-vendor  # web group plus the vendor tier
  search.py --list-providers

Exit codes: 0 ok (at least one provider answered) · 2 all providers failed
"""
import argparse
import html as html_lib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar as cookiejar
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

NO_OUTER_RETRY = {"ddg", "ddg_lite", "bing",
                  "keenable", "firecrawl", "exa"}   # internal retries / no double-hammering

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
TIMEOUT = 20
RETRIES = 2


# --------------------------------------------------------------------- plumbing
# One cookie jar for the whole run. Yahoo's front end answers 500 until it has
# set a session cookie, and only a client that *keeps* that cookie sees the
# retry succeed — plain urlopen throws the Set-Cookie away and 500s forever.
_COOKIES = cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_COOKIES))


# A search response is JSON or HTML, never a download. Capping the read keeps a
# hostile or broken endpoint from turning one query into an out-of-memory kill,
# and it is the single place every provider's body passes through.
MAX_BODY = 4 * 1024 * 1024


class BodyTooLarge(RuntimeError):
    """The response was bigger than we are willing to hold in memory."""


def _decode_capped(resp, cap=None, what="response"):
    cap = MAX_BODY if cap is None else cap
    raw = resp.read(cap + 1)
    if len(raw) > cap:
        raise BodyTooLarge(f"{what} over {cap // 1048576} MB, refused")
    return raw.decode("utf-8", "replace")


def http(url, headers=None, timeout=TIMEOUT):
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with _OPENER.open(req, timeout=timeout) as r:
        return _decode_capped(r, what="search body")


def with_retries(fn, what, retries=RETRIES):
    last = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (400, 401, 403, 404):
                break
        except Exception as e:
            last = str(e)[:120]
        time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"{what}: {last}")


def item(title, url, snippet="", provider="", rank=0, extra=None, cap=500):
    d = {"title": (title or "").strip()[:300],
         "url": (url or "").strip(),
         "snippet": re.sub(r"\s+", " ", (snippet or "")).strip()[:cap],
         "provider": provider,
         "rank": rank}
    if extra:
        d.update(extra)
    return d


def xml_items(raw):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag in ("item", "entry"):
            out.append(it)
    return out


def child(node, *names):
    for n in names:
        for c in node:
            if c.tag.split("}")[-1] == n:
                return (c.text or "").strip()
        for c in node:
            if c.tag.split("}")[-1] == n:
                for cc in c:
                    if cc.tag.split("}")[-1] == "href":
                        return cc.get("href") or ""
    return ""


# --------------------------------------------------------------------- providers
# ---------------------------------------------------------------- engine search
# Every general engine blocks bare clients from datacenter IPs (we tested them
# all: 202 / 403 / challenge). Two ways through, tried in order:
#   1. r.jina.ai  — renders the SERP from its own IP, returns markdown
#   2. CORS proxies that relay the raw HTML — allorigins, cors.lol, codetabs
# Any of them can disappear or rate-limit you; that is why there is a list.
JINA = "https://r.jina.ai/"
HTML_PROXIES = [
    ("allorigins", "https://api.allorigins.win/raw?url={}"),
    ("cors.lol", "https://api.cors.lol/?url={}"),
    ("codetabs", "https://api.codetabs.com/v1/proxy?quest={}"),
]
SKIP_LINK = re.compile(r"(duckduckgo\.com/(html|l/\?|y\.js)|bing\.com/(search|copilotsearch|images)|"
                       r"th\.bing\.com|r\.jina\.ai|external-content\.duckduckgo|"
                       r"microsoft\.com/edge|go\.microsoft|/favicon|\bicon\b)", re.I)
WRAPPED = re.compile(r"(duckduckgo\.com/l/\?|bing\.com/ck/a)", re.I)


def _unwrap_engine_link(url):
    """DDG wraps links as /l/?uddg=..., Bing as /ck/a?...&u=a1<base64>."""
    import base64
    if url.startswith("//"):
        url = "https:" + url
    m = re.search(r"[?&]uddg=([^&]+)", url)
    if m:
        return urllib.parse.unquote(html_lib.unescape(m.group(1)))
    m = re.search(r"/RU=([^/]+)/", url)
    if m:
        return urllib.parse.unquote(m.group(1))
    m = re.search(r"[?&]u=a1([A-Za-z0-9_\-]+)", url)
    if m:
        try:
            blob = base64.urlsafe_b64decode(m.group(1) + "=" * (-len(m.group(1)) % 4))
            found = re.findall(rb"https?://[\x20-\x7e]{6,400}", blob)
            if found:
                return found[0].decode("utf-8", "replace").rstrip("\x00")
        except Exception:
            pass
    return url


def _clean(text, limit=300):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _ok_title(t):
    tl = t.lower()
    if len(t) < 6:
        return False
    return not (tl.startswith(("image", "skip", "accessibility", "global web")) or
                "web icon" in tl or tl in ("ai mode", "sign in", "share", "feedback", "menu"))


# ---- transport -------------------------------------------------------------
_JINA_SLOTS = threading.Semaphore(3)
_JINA_STATE = {"dead": False, "why": ""}
_PROXY_ORDER = list(HTML_PROXIES)          # most-recently-successful first


def _jina(url, timeout=30):
    """Reader proxy. Provides markdown from its own IP; may refuse datacenter
    traffic (401) at any time — once it does we stop asking for the rest of
    the run instead of burning the timeout on every provider."""
    if _JINA_STATE["dead"]:
        raise RuntimeError(f"reader unavailable ({_JINA_STATE['why']})")
    with _JINA_SLOTS:
        raw = http(JINA + url, timeout=timeout, headers={"Accept": "text/plain"})
    if len(raw) < 200:
        raise RuntimeError(f"reader returned {len(raw)}B")
    return raw


_COOLDOWN = {}                      # proxy name -> monotonic time when usable again
COOL_FOR = 75                       # seconds to leave a rate-limited proxy alone


def _via_html_proxy(url, budget=40):
    """Relay the raw SERP HTML through a CORS proxy. These are volunteer
    services: they 429/502/522 for a minute, then work again. So each gets two
    tries, failures put it in a cooldown, and the whole attempt shares a budget.
    """
    global _PROXY_ORDER
    deadline = time.time() + budget
    errors = []
    order = [p for p in _PROXY_ORDER if _COOLDOWN.get(p[0], 0) <= time.time()]
    order += [p for p in _PROXY_ORDER if p not in order]      # still try, last
    for name, tpl in order:
        for attempt in range(2):
            left = deadline - time.time()
            if left < 3:
                errors.append(f"{name}: out of budget")
                break
            try:
                raw = http(tpl.format(urllib.parse.quote(url, safe="")),
                           timeout=min(18, left))
                if len(raw) > 500 and "<" in raw[:300]:
                    _COOLDOWN.pop(name, None)
                    if _PROXY_ORDER[0][0] != name:             # promote the winner
                        _PROXY_ORDER.sort(key=lambda p: p[0] != name)
                    return raw
                errors.append(f"{name}: {len(raw)}B")
            except urllib.error.HTTPError as e:
                errors.append(f"{name}: HTTP {e.code}")
                if e.code in (401, 402, 403, 429) or e.code >= 500:
                    _COOLDOWN[name] = time.time() + COOL_FOR
                    break
            except Exception as e:
                errors.append(f"{name}: {str(e)[:50]}")
                _COOLDOWN[name] = time.time() + COOL_FOR // 2
                break
            time.sleep(0.8)
    raise RuntimeError("no proxy relayed: " + "; ".join(errors[-3:]))


# ---- parsers ---------------------------------------------------------------
def md_results(md, provider):
    """Parser for any engine rendered as markdown by the reader."""
    out = []
    for m in re.finditer(r"\[([^\]]{3,200})\]\((https?://[^)\s]+)\)", md):
        title, url = m.group(1), m.group(2)
        if WRAPPED.search(url) or SKIP_LINK.search(url):
            url = _unwrap_engine_link(url)
            if not url or SKIP_LINK.search(url) or WRAPPED.search(url):
                continue
        title = re.sub(r"\*\*|__|`", "", title).strip()
        title = re.sub(r"^!\[.*?\]\s*", "", title)
        if not _ok_title(title):
            continue
        tail = md[m.end():m.end() + 320]
        tail = re.split(r"\n## \[|\n\d+\.\[", tail)[0]
        tail = re.sub(r"\[!\[.*?\]\(.*?\)\]\(.*?\)", " ", tail)
        tail = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", tail)
        snip = re.sub(r"[#*>|]+", " ", tail)
        out.append(item(title, url, _clean(snip, 400), provider))
    return out


_A_RE = re.compile(r"<a\s([^>]*)>(.*?)</a>", re.S | re.I)
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
_CLASS_RE = re.compile(r"""class\s*=\s*["']([^"']*)["']""", re.I)


def _anchors(html):
    """Yield (class, href, inner_html) for every anchor. Attribute order and
    quote style vary between DDG surfaces and change without notice."""
    for m in _A_RE.finditer(html):
        attrs, inner = m.group(1), m.group(2)
        h = _HREF_RE.search(attrs)
        if not h:
            continue
        c = _CLASS_RE.search(attrs)
        yield (c.group(1) if c else ""), h.group(1), inner


def html_results(html, provider, engine):
    """Parser for raw SERP HTML relayed by a proxy (or served directly)."""
    if engine == "bing":
        pairs = []
        for block in re.findall(r"<h2[^>]*>(.*?)</h2>", html, re.S | re.I):
            for cls, href, inner in _anchors(block):
                pairs.append((href, inner))
                break
        snips = re.findall(r'<p class="b_lineclamp[^"]*"[^>]*>(.*?)</p>', html, re.S)
    else:
        a_cls, s_cls = ("result__a", "result__snippet") if engine == "ddg" \
            else ("result-link", "result-snippet")
        pairs = [(href, inner) for cls, href, inner in _anchors(html) if a_cls in cls]
        tag_end = "</a>" if engine == "ddg" else "</td>"
        snips = re.findall(r"""class=["']%s["'][^>]*>(.*?)%s""" % (s_cls, tag_end), html, re.S)
    out = []
    for i, (url, raw_title) in enumerate(pairs):
        real = _unwrap_engine_link(html_lib.unescape(url))
        if not real.startswith("http") or SKIP_LINK.search(real):
            continue
        title = _clean(raw_title, 200)
        if not _ok_title(title):
            continue
        snip = _clean(snips[i], 400) if i < len(snips) else ""
        out.append(item(title, real, snip, provider))
    return out


def _direct_serp(url, timeout=12):
    """Some days the engines answer us directly from this IP; their blocks are
    not stable. Cheap to test first (12 s) and much faster than any relay."""
    return http(url, timeout=timeout, headers={
        "Accept": "text/html,application/xhtml+xml",
        "Referer": "https://duckduckgo.com/"})


STOP_Q = set("""a an the and or but if then than that this these those of in on at to for
from with without into over under why how what when where which who whom whose does do did
is are was were be been being can could should would will shall may might must fix best
use using used all any not no yes it its as by about more most other some such only own
same so too very just also still work works working get gets got make makes made""".split())


def _on_topic(rows, q, floor=0.34):
    """Quality gate. A blocked or degraded engine will happily return a page of
    dictionary entries for the word "why" — plausible HTML, useless results.
    If fewer than a third of the hits share any distinctive query word, we treat
    the answer as junk and let the next transport try."""
    terms = {w for w in re.findall(r"[a-z0-9][a-z0-9\-.]{3,}", q.lower()) if w not in STOP_Q}
    if len(terms) < 2 or not rows:
        return True
    hits = 0
    for r in rows:
        blob = (r.get("title", "") + " " + r.get("snippet", "")).lower()
        if any(t in blob for t in terms):
            hits += 1
    return hits / len(rows) >= floor


def _engine_search(q, provider, engine, urls):
    """direct -> reader -> CORS proxies, first usable answer wins."""
    errors = []
    for u in urls:
        try:
            rows = html_results(_direct_serp(u), provider, engine)
            if rows and _on_topic(rows, q):
                return rows
            errors.append("direct: off-topic" if rows else "direct: 0 results")
        except urllib.error.HTTPError as e:
            errors.append(f"direct: HTTP {e.code}")
        except Exception as e:
            errors.append(f"direct: {str(e)[:50]}")
    try:
        md = _jina("https://www.bing.com/search?" + urllib.parse.urlencode({"q": q, "count": 30})
                   if engine == "bing" else urls[0], timeout=25)
        rows = md_results(md, provider)
        if rows and _on_topic(rows, q):
            return rows
        errors.append("reader: off-topic" if rows else "reader: 0 results")
    except urllib.error.HTTPError as e:
        if e.code in (401, 402, 403, 429):
            _JINA_STATE["dead"], _JINA_STATE["why"] = True, f"HTTP {e.code}"
        errors.append(f"reader: HTTP {e.code}")
    except Exception as e:
        errors.append(f"reader: {str(e)[:60]}")
    for u in urls:
        try:
            rows = html_results(_via_html_proxy(u), provider, engine)
            if rows and _on_topic(rows, q):
                return rows
            errors.append("proxy: off-topic" if rows else "proxy: 0 results")
        except Exception as e:
            errors.append(str(e)[:70])
    raise RuntimeError(" | ".join(errors[-3:]))


def p_ddg(q, n):
    """DuckDuckGo html endpoint: direct, then reader, then CORS relays."""
    qs = urllib.parse.urlencode({"q": q, "kl": "us-en"})
    rows = _engine_search(q, "ddg", "ddg",
                          [f"https://html.duckduckgo.com/html/?{qs}"])
    return rows[:n]


def p_ddg_lite(q, n):
    """The lite surface: less blocked, older markup. Spare tyre for ddg."""
    qs = urllib.parse.urlencode({"q": q, "kl": "us-en"})
    try:
        rows = _engine_search(q, "ddg", "ddg_lite",
                              [f"https://lite.duckduckgo.com/lite/?{qs}"])
        return rows[:n]
    except Exception as e:
        raise RuntimeError(f"ddg_lite: {e}")


def p_bingweb(q, n):
    url = "https://www.bing.com/search?" + urllib.parse.urlencode(
        {"q": q, "count": max(20, n), "setlang": "en"})
    rows = _engine_search(q, "bing", "bing", [url])
    return rows[:n]


def p_bing_rss(q, n):
    """Bing's RSS output — the only general web index that still serves a bot."""
    raw = http("https://www.bing.com/search?" + urllib.parse.urlencode(
        {"q": q, "format": "rss", "count": n}))
    out = []
    for it in xml_items(raw)[:n]:
        out.append(item(child(it, "title"), child(it, "link"),
                        child(it, "description"), "bing"))
    return out


def p_yahoo(q, n):
    """Yahoo's web results — Bing's index behind a front end that still answers bots.

    Bing serves junk to suspected bots on some networks (our relevance gate drops
    it) while this endpoint, same index, returns the real thing. Measured
    2026-11-02: 3/3 queries, 0.8 s, relevant — where bing/ddg/google were
    blocked or useless from the same host.
    """
    raw = http("https://search.yahoo.com/search?" + urllib.parse.urlencode(
        {"p": q, "n": min(max(n, 10), 30), "ei": "UTF-8"}))
    out = []
    for block in re.split(r'<div class="[^"]*\balgo-sr\b[^"]*"', raw)[1:]:
        a = (re.search(r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
             or re.search(r'<a[^>]+href="([^"]+)"[^>]*>(?:(?!</a>).)*?<h3[^>]*>(.*?)</h3>', block, re.S))
        if not a:
            continue
        snip = (re.search(r'<div class="compText[^"]*"[^>]*>\s*<p[^>]*>(.*?)</p>', block, re.S)
                or re.search(r'<p class="[^"]*fz-ms[^"]*"[^>]*>(.*?)</p>', block, re.S)
                or re.search(r"<p[^>]*>(.*?)</p>", block, re.S))
        url = _unwrap_engine_link(html_lib.unescape(a.group(1).strip()))
        if (urllib.parse.urlparse(url).netloc or "").endswith("yahoo.com"):
            continue
        out.append(item(_clean(a.group(2), 200), url,
                        _clean(snip.group(1)) if snip else "", "yahoo", len(out) + 1))
        if len(out) >= n:
            break
    return out


def _unwrap_gnews(u):
    """Google News RSS links are base64 protobuf wrappers; dig the real URL out."""
    m = re.search(r"/articles/([A-Za-z0-9_-]+)", u or "")
    if not m:
        return u
    try:
        import base64
        blob = base64.urlsafe_b64decode(m.group(1) + "=" * (-len(m.group(1)) % 4))
        found = re.findall(rb"https?://[\x20-\x7e]{6,300}", blob)
        if found:
            return found[0].decode("utf-8", "replace").rstrip("\x00")
    except Exception:
        pass
    return u


def p_gnews(q, n):
    raw = http("https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}))
    out = []
    for it in xml_items(raw)[:n]:
        src_name, src_url = "", ""
        for c in it:
            if c.tag.split("}")[-1] == "source":
                src_name, src_url = (c.text or "").strip(), c.get("url") or ""
        u = _unwrap_gnews(child(it, "link"))
        # Google News hides the article behind an opaque token: keep the redirect,
        # but remember the publisher so `resolve()` can find the real URL later.
        out.append(item(child(it, "title"), u, child(it, "pubDate"), "gnews",
                        extra={"publisher": src_name, "publisher_url": src_url,
                               "needs_resolve": "news.google.com" in u}))
    return out


def p_bingnews(q, n):
    """Bing News RSS — real article URLs (hidden behind an apiclick wrapper we unwrap)."""
    raw = http("https://www.bing.com/news/search?" + urllib.parse.urlencode(
        {"q": q, "format": "rss"}))
    out = []
    for it in xml_items(raw)[:n]:
        link = child(it, "link")
        m = re.search(r"[?&]url=([^&]+)", link)
        if m:
            link = urllib.parse.unquote(m.group(1))
        out.append(item(child(it, "title"), link, child(it, "description") or
                        child(it, "pubDate"), "bingnews"))
    return out


def p_wikipedia(q, n):
    """Encyclopaedia lookups only: full-text search on a long sentence matches noise."""
    words = [w for w in re.findall(r"\w{3,}", q) if w not in ("the", "and", "for", "are", "with")]
    if len(words) > 6:
        return []
    raw = http("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": q, "format": "json",
         "srlimit": min(n, 3), "srwhat": "text", "srprop": "snippet"}))
    js = json.loads(raw)
    out = []
    for r in js.get("query", {}).get("search", []):
        snip = re.sub(r"<[^>]+>", "", r.get("snippet", ""))
        out.append(item(r.get("title"),
                        "https://en.wikipedia.org/wiki/" +
                        urllib.parse.quote(r.get("title", "").replace(" ", "_")),
                        snip, "wikipedia"))
    return out


def p_hn(q, n):
    raw = http("https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode(
        {"query": q, "tags": "story", "hitsPerPage": n}))
    js = json.loads(raw)
    out = []
    for h in js.get("hits", [])[:n]:
        out.append(item(h.get("title"), h.get("url") or
                        f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                        f"{h.get('points', 0)} points, {h.get('num_comments', 0)} comments",
                        "hn"))
    return out


def p_stackexchange(q, n):
    raw = http("https://api.stackexchange.com/2.3/search/advanced?" +
               urllib.parse.urlencode({"order": "desc", "sort": "relevance", "q": q,
                                       "site": "stackoverflow", "pagesize": n,
                                       "filter": "withbody"}),
               headers={"Accept-Encoding": "identity"})
    js = json.loads(raw)
    out = []
    for it_ in js.get("items", [])[:n]:
        body = re.sub(r"<[^>]+>", " ", it_.get("body", ""))
        out.append(item(it_.get("title"), it_.get("link"),
                        f"score {it_.get('score')} · " + re.sub(r"\s+", " ", body),
                        "stackexchange"))
    return out


def p_github(q, n):
    raw = http("https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        {"q": q, "per_page": n, "sort": "stars"}),
        headers={"Accept": "application/vnd.github+json"})
    js = json.loads(raw)
    out = []
    for r in js.get("items", [])[:n]:
        out.append(item(r.get("full_name"), r.get("html_url"),
                        f"★{r.get('stargazers_count')} · {(r.get('description') or '')}",
                        "github"))
    return out


def p_arxiv(q, n):
    raw = http("https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": f"all:{q}", "max_results": n, "sortBy": "relevance"}))
    out = []
    for e in xml_items(raw)[:n]:
        out.append(item(child(e, "title"), child(e, "id"),
                        child(e, "summary"), "arxiv"))
    return out


def p_openalex(q, n):
    raw = http("https://api.openalex.org/works?" + urllib.parse.urlencode(
        {"search": q, "per_page": n, "mailto": "agent@example.com"}))
    js = json.loads(raw)
    out = []
    for w in js.get("results", [])[:n]:
        loc = (w.get("primary_location") or {}).get("landing_page_url") or w.get("id")
        out.append(item(w.get("display_name"), loc,
                        f"{w.get('publication_year')} · cited by {w.get('cited_by_count')}",
                        "openalex"))
    return out


def p_europepmc(q, n):
    raw = http("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" +
               urllib.parse.urlencode({"query": q, "format": "json", "pageSize": n}))
    js = json.loads(raw)
    out = []
    for r in js.get("resultList", {}).get("result", [])[:n]:
        out.append(item(r.get("title"),
                        f"https://europepmc.org/article/{r.get('source', 'MED')}/{r.get('id')}",
                        r.get("abstractText", "") or r.get("journalTitle", ""),
                        "europepmc"))
    return out


def p_crossref(q, n):
    raw = http("https://api.crossref.org/works?" + urllib.parse.urlencode(
        {"query": q, "rows": n, "select": "title,URL,abstract,issued,is-referenced-by-count"}))
    js = json.loads(raw)
    out = []
    for w in js.get("message", {}).get("items", [])[:n]:
        title = (w.get("title") or [""])[0]
        out.append(item(title, w.get("URL"), re.sub(r"<[^>]+>", " ",
                    w.get("abstract", "") or ""), "crossref"))
    return out


def p_npm(q, n):
    raw = http("https://registry.npmjs.org/-/v1/search?" + urllib.parse.urlencode(
        {"text": q, "size": n}))
    js = json.loads(raw)
    out = []
    for o in js.get("objects", [])[:n]:
        p = o.get("package", {})
        out.append(item(p.get("name"), p.get("links", {}).get("npm") or
                        f"https://www.npmjs.com/package/{p.get('name')}",
                        p.get("description", ""), "npm"))
    return out


def p_pypi(q, n):
    """Package lookup, not a search: pypi/<name>/json."""
    name = q.strip().split()[-1]
    raw = http(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json")
    js = json.loads(raw)
    i = js.get("info", {})
    return [item(i.get("name"), i.get("package_url") or i.get("home_page", ""),
                 f"v{i.get('version')} · {i.get('summary', '')}", "pypi")]


def p_ddg_ia(q, n):
    raw = http("https://api.duckduckgo.com/?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "no_html": 1, "skip_disambig": 1}))
    js = json.loads(raw)
    out = []
    if js.get("AbstractText"):
        out.append(item(js.get("Heading") or q, js.get("AbstractURL", ""),
                        js.get("AbstractText"), "ddg_ia"))
    for t in (js.get("RelatedTopics") or [])[:max(0, n - len(out))]:
        if isinstance(t, dict) and t.get("Text"):
            out.append(item(t.get("Text")[:80], t.get("FirstURL", ""), t.get("Text"), "ddg_ia"))
    return out



# ------------------------------------------------------------------ vendor tier
# These three call vendor search APIs that answer without a credential. They are
# opt-in (--group vendor / --with-vendor) because they are borrowed
# infrastructure: undocumented, unversioned, and able to vanish without notice.
# Verified 2026-11-02: 8 results each, 0.2-0.5 s, no throttling at 6 sequential
# and 5 concurrent calls. Never make one of them the only path.
VENDOR_UA = "keyless-agent-skills/1.3 (+https://github.com/aminmzadev/keyless-agent-skills)"
# Keenable wants an app identifier; it is ours, not somebody else's product name.
VENDOR_TITLE = "keyless-agent-skills"
VENDOR_SNIPPET_CAP = 1200      # their value is the excerpt, not the link

KEENABLE_URL = "https://api.keenable.ai/v1/search/public"
FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v2/search"
EXA_MCP_URL = "https://mcp.exa.ai/mcp"


def _endpoint(default, env):
    """Tests and self-hosted gateways point these at a local server."""
    return os.environ.get(env, default)


def _post_json(url, payload, headers=None, timeout=25):
    h = {"User-Agent": VENDOR_UA, "Content-Type": "application/json",
         "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(_decode_capped(r, what="vendor body"))


def p_keenable(q, n):
    """Keenable public search — fastest general index measured here (0.2 s), long excerpts."""
    js = _post_json(_endpoint(KEENABLE_URL, "KEENABLE_SEARCH_URL"),
                    {"query": q, "max_results": max(1, n)},
                    {"X-Keenable-Title": VENDOR_TITLE})
    rows = js.get("results") or js.get("data") or []
    out = []
    for i, r in enumerate(rows[:n]):
        if not isinstance(r, dict):
            continue
        out.append(item(r.get("title") or "", r.get("url") or "",
                        r.get("snippet") or r.get("description") or "",
                        "keenable", i + 1, cap=VENDOR_SNIPPET_CAP))
    return [r for r in out if r["url"]]


def p_firecrawl(q, n):
    """Firecrawl's public search endpoint — called with no Authorization header."""
    js = _post_json(_endpoint(FIRECRAWL_SEARCH_URL, "FIRECRAWL_SEARCH_URL"),
                    {"query": q, "limit": max(1, n)})
    data = js.get("data") or {}
    rows = data.get("web") if isinstance(data, dict) else None
    rows = rows or (data if isinstance(data, list) else []) or []
    out = []
    for i, r in enumerate(rows[:n]):
        if not isinstance(r, dict):
            continue
        out.append(item(r.get("title") or "", r.get("url") or "",
                        r.get("description") or r.get("snippet") or "",
                        "firecrawl", i + 1, cap=VENDOR_SNIPPET_CAP))
    return [r for r in out if r["url"]]


def _parse_exa_text(text, n):
    """Exa's MCP tool answers in markdown-ish blocks: Title / URL / Highlights."""
    out = []
    blocks = re.split(r"(?m)^\s*Title:\s*", text)
    for blk in blocks[1:]:
        title = blk.splitlines()[0].strip() if blk.strip() else ""
        m = re.search(r"(?m)^\s*URL:\s*(\S+)", blk)
        if not m:
            continue
        url = m.group(1).strip()
        body = blk[m.end():]
        body = re.sub(r"(?m)^\s*(Text|Highlights|Published Date|Author):\s*", "", body)
        out.append(item(title, url, body, "exa", len(out) + 1, cap=VENDOR_SNIPPET_CAP))
        if len(out) >= n:
            break
    return out


def p_exa(q, n):
    """Exa via its public MCP endpoint, JSON-RPC tools/call (search only).

    Extract is deliberately absent: measured on six pages it returned a constant
    ~3,100 characters, i.e. a hard truncation, which makes it useless as a reader.
    """
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "web_search_exa",
                          "arguments": {"query": q, "numResults": max(1, n)}}}
    h = {"User-Agent": VENDOR_UA, "Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}
    req = urllib.request.Request(_endpoint(EXA_MCP_URL, "EXA_MCP_URL"),
                                 data=json.dumps(payload).encode(), headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = _decode_capped(r, what="exa body")
    text = ""
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = ((obj.get("result") or {}).get("content") or [])
        if content and isinstance(content[0], dict):
            text = content[0].get("text") or ""
            break
    if not text:
        raise RuntimeError("exa: unparseable MCP payload")
    rows = _parse_exa_text(text, n)
    if not rows:
        raise RuntimeError("exa: no parsable results")
    return rows


PROVIDERS = {
    "bing": (p_bingweb, "web"), "ddg": (p_ddg, "web"), "ddg_lite": (p_ddg_lite, "spare"),
    "bingnews": (p_bingnews, "web"), "gnews": (p_gnews, "web"),
    "wikipedia": (p_wikipedia, "web"), "ddg_ia": (p_ddg_ia, "web"),
    "bing_rss": (p_bing_rss, "web"),
    "hn": (p_hn, "tech"), "stackexchange": (p_stackexchange, "tech"),
    "github": (p_github, "tech"), "npm": (p_npm, "tech"), "pypi": (p_pypi, "tech"),
    "arxiv": (p_arxiv, "academic"), "openalex": (p_openalex, "academic"),
    "europepmc": (p_europepmc, "academic"), "crossref": (p_crossref, "academic"),
    "yahoo": (p_yahoo, "web"),
    "keenable": (p_keenable, "vendor"), "firecrawl": (p_firecrawl, "vendor"),
    "exa": (p_exa, "vendor"),
}
GROUPS = {
    # bing_rss needs no relay at all: when the relays are throttled it is the
    # difference between "nothing" and "something". It is noisy, but the
    # off-topic gate above now throws the noise away.
    "web": ["bing", "ddg", "yahoo", "wikipedia", "bing_rss"],
    "tech": ["hn", "stackexchange", "github"],
    "academic": ["arxiv", "openalex", "europepmc"],
    # vendor APIs: no key, no account, but borrowed infrastructure with no SLA.
    # Opt-in on purpose (--group vendor or --with-vendor), never in "all".
    "vendor": ["keenable", "firecrawl", "exa"],
    # pypi is a package *lookup*, not a search — excluded from "all" on purpose
    # ddg_lite is the same engine through the same proxies: a manual spare
    "all": [k for k in PROVIDERS if k not in ("pypi", "ddg_lite", "keenable", "firecrawl", "exa")],
}


# ----------------------------------------------------------------------- merging
def normalize(rows, provider=""):
    """Coerce one provider's output into the shape the rest of the pipeline expects.

    `dedupe()` indexes r["url"] and `rank()` indexes r["title"]: a provider that
    returns a bare string, or a dict without a url, used to take the *whole*
    search down with a KeyError/TypeError, because the merge runs outside the
    per-provider try. The three shipped vendor providers guard themselves; the
    registry is documented as an extension point, so the contract is enforced
    here instead — one boundary, every provider, no exceptions.
    """
    out = []
    for r in (rows if isinstance(rows, list) else []):
        if not isinstance(r, dict):
            continue
        row = item(r.get("title") or "", r.get("url") or "", r.get("snippet") or "",
                   r.get("provider") or provider, r.get("rank") or 0)
        for k, v in r.items():          # keep provider-specific extras (published, extra, ...)
            row.setdefault(k, v)
        if not (row["url"] or row["title"]):
            continue
        out.append(row)
    return out


def norm_url(u):
    u = re.sub(r"^https?://", "", (u or "").lower())
    u = re.sub(r"^www\.", "", u)
    u = re.sub(r"[?#].*$", "", u)
    return u.rstrip("/")


def dedupe(rows):
    seen, out = {}, []
    for r in rows:
        key = norm_url(r["url"]) or r["title"].lower()[:60]
        if not key:
            continue
        if key in seen:
            if r["provider"] not in seen[key]["providers"]:
                seen[key]["providers"].append(r["provider"])
            if len(r["snippet"]) > len(seen[key]["snippet"]):
                seen[key]["snippet"] = r["snippet"]
            continue
        r["providers"] = [r["provider"]]
        seen[key] = r
        out.append(r)
    return out


STOP = {"the", "and", "for", "with", "how", "what", "why", "when", "does", "can",
        "you", "are", "was", "not", "but", "from", "into", "that", "this", "get",
        "use", "using", "best", "free", "about", "your", "our", "its", "his", "her"}


def rank(rows, query):
    terms = [t for t in re.findall(r"\w{3,}", query.lower()) if t not in STOP]

    def score(r):
        blob = ((r["title"] or "") + " " + (r["snippet"] or "")).lower()
        hits = sum(1 for w in terms if w in blob)
        s = hits * 1.6
        s += len(set(r.get("providers", []))) * 2.0       # cross-provider agreement
        s += sum(1.2 for w in terms if w in (r["title"] or "").lower())
        if hits == 0:
            s -= 4.0                                       # relevance gate
        elif not any(w in (r["title"] or "").lower() for w in terms):
            s -= 3.0                                       # body-only match: weak
        if r["provider"] in ("wikipedia", "github", "stackexchange", "arxiv"):
            s += 0.4
        if len(r["snippet"] or "") > 120:
            s += 0.3
        return -s

    rows.sort(key=score)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def trim(rows, limit, per_domain=2):
    """Keep the list diverse: no single site should own the results."""
    out, seen = [], {}
    for r in rows:
        d = norm_url(r["url"]).split("/")[0] if r["url"] else ""
        if seen.get(d, 0) >= per_domain:
            continue
        seen[d] = seen.get(d, 0) + 1
        out.append(r)
        if limit and len(out) >= limit:
            break
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


# ------------------------------------------------------------------- search cache
CACHE_ROOT = os.environ.get("AGENT_SKILLS_CACHE") or os.path.expanduser(
    "~/.cache/agent-skills")
CACHE_DIR = os.path.join(CACHE_ROOT, "search")
CACHE_TTL = 3600
CACHE_SCHEMA = 2        # bump when the payload shape changes; old entries are ignored

try:                    # the shared cache layer, vendored next to us
    from _cachekit import atomic_write_json as _atomic_write_json
    from _cachekit import pos_int
    from _cachekit import read_json as _read_json
except ImportError:     # a lone copy of search.py: same behaviour
    def pos_int(text):                              # noqa: D103 - see _cachekit
        import argparse
        n = int(text)
        if n < 1:
            raise argparse.ArgumentTypeError(f"must be 1 or more (got {n})")
        return n

    def _atomic_write_json(path, obj, **kw):        # noqa: D103 - see _cachekit
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp-{os.getpid()}"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, **kw)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _read_json(path, default=None):             # noqa: D103 - see _cachekit
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default


def _cache_path(key):
    import hashlib
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode()).hexdigest() + ".json")


def _cache_get(key):
    d = _read_json(_cache_path(key))
    if not isinstance(d, dict) or d.get("schema") != CACHE_SCHEMA:
        return None
    if time.time() - d.get("_ts", 0) <= CACHE_TTL:
        return d.get("payload")
    return None


def _cache_put(key, payload):
    try:
        _atomic_write_json(_cache_path(key), {"_ts": time.time(), "schema": CACHE_SCHEMA,
                                              "payload": payload})
    except OSError:
        pass        # a cache we cannot write must never fail the search


# -------------------------------------------------------------------------- main
def search(query, providers=None, group="web", limit=10, parallel=6, use_cache=True):
    names = providers or GROUPS.get(group, GROUPS["web"])
    key = json.dumps([query, names, limit])
    if use_cache:
        hit = _cache_get(key)
        if hit:
            hit["cached"] = True
            return hit
    jobs, results, errors = {}, [], {}
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        for name in names:
            fn = PROVIDERS.get(name, (None,))[0]
            if not fn:
                errors[name] = "unknown provider"
                continue
            rt = 0 if name in NO_OUTER_RETRY else RETRIES
            jobs[ex.submit(lambda f=fn, n=name, r=rt: with_retries(lambda: f(query, limit), n,
                                                                   retries=r))] = name
        for fut in as_completed(jobs):
            name = jobs[fut]
            try:
                results.extend(normalize(fut.result(), name))
            except Exception as e:
                errors[name] = str(e)[:150]
    if not results:
        # relax: drop the least distinctive words and try once more
        words = [w for w in re.findall(r"\w{3,}", query.lower()) if w not in STOP]
        short = " ".join(words[:3]) if len(words) > 3 else ""
        if short and short != query.lower():
            relaxed = search(short, providers=names, group=group, limit=limit,
                             parallel=parallel, use_cache=use_cache)
            relaxed["relaxed_from"] = query
            return relaxed
    rows = trim(rank(dedupe(results), query), limit)
    payload = {"query": query, "providers_used": names, "errors": errors,
               "count": len(rows), "results": rows}
    # don't freeze a bad run: if most providers errored, the answer is an
    # artefact of the proxies being down, not a fact about the web
    if use_cache and rows and len(errors) <= max(1, len(names) // 2):
        _cache_put(key, payload)
    return payload


def resolve(title, limit=5):
    """GNews gives us opaque redirects. Find the real article by searching its title."""
    title = re.sub(r"\s*-\s*[^-]{2,40}$", "", (title or "")).strip()   # drop " - Publisher"
    if not title:
        return None
    try:
        hits = with_retries(lambda: p_bing(f'"{title}"', limit), "resolve")
    except Exception:
        return None
    want = set(re.findall(r"\w{4,}", title.lower()))
    best, best_score = None, 0.0
    for h in hits:
        if "news.google.com" in (h["url"] or ""):
            continue
        have = set(re.findall(r"\w{4,}", (h["title"] or "").lower()))
        if not want:
            continue
        score = len(want & have) / len(want)
        if score > best_score:
            best, best_score = h, score
    return best["url"] if best and best_score >= 0.6 else None


def cmd_list(a):
    for g, names in GROUPS.items():
        if g == "all":
            continue
        print(f"{g}:")
        for n in names:
            doc = (PROVIDERS[n][0].__doc__ or "").strip().splitlines()
            print(f"  {n:<15}{doc[0] if doc else ''}")


def main():
    ap = argparse.ArgumentParser(description="keyless multi-provider web search")
    ap.add_argument("query", nargs="*")
    ap.add_argument("--provider", action="append", default=None)
    ap.add_argument("--group", default="web", choices=list(GROUPS))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--with-vendor", action="store_true",
                    help="add the vendor tier (keenable, firecrawl, exa) to the chosen group")
    ap.add_argument("--limit", type=pos_int, default=10)
    ap.add_argument("--parallel", type=pos_int, default=6)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--list-providers", action="store_true")
    a = ap.parse_args()

    if a.list_providers:
        return cmd_list(a)
    q = " ".join(a.query).strip()
    if not q:
        ap.error("give a query")

    group = "all" if a.all else a.group
    providers = a.provider
    if a.with_vendor and not providers:
        providers = list(dict.fromkeys(GROUPS.get(group, GROUPS["web"]) + GROUPS["vendor"]))
    res = search(q, providers=providers, group=group, limit=a.limit,
                 parallel=a.parallel, use_cache=not a.no_cache)

    if a.json:
        print(json.dumps(res, indent=1))
    else:
        if res["errors"]:
            print("# degraded: " + ", ".join(f"{k} ({v[:60]})" for k, v in res["errors"].items()),
                  file=sys.stderr)
        for r in res["results"]:
            print(f"{r['rank']:>2}. {r['title'][:95]}")
            print(f"    {r['url'][:110]}")
            if r["snippet"]:
                print(f"    {r['snippet'][:150]}")
            print(f"    via {','.join(r['providers'])}")
    return 0 if res["count"] or not res["errors"] else 2


if __name__ == "__main__":
    sys.exit(main() or 0)
