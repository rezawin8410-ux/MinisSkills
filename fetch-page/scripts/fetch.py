#!/usr/bin/env python3
# ---
# Published as the `fetch-page` skill. Canonical source: deep-research/scripts/.
# Edit there, then run ./sync.sh (synced 2026-11-02).
# ---
"""
fetch.py — turn any URL into clean markdown for an agent, with four fallbacks.

  1. direct      : urllib + browser UA, gzip, redirect following, HTML->text
  2. reader      : r.jina.ai markdown -> CORS-proxied HTML (allorigins, cors.lol,
                   codetabs), whichever answers first
  3. vendor      : Firecrawl's anonymous scrape endpoint — renders JS, survives
                   Cloudflare, and is the only stage that reaches some pages
  4. exa         : Exa's anonymous MCP web_fetch_exa — second reader of last
                   resort, returns the page's own markdown
  5. archive     : Wayback Machine snapshot, for pages that are gone or blocked

Every call has a hard wall-clock deadline (--deadline, default 45 s). Individual
stages already carry socket timeouts, but a dead relay or a DNS black hole can
ignore them; the deadline is enforced from outside the stage so a hostile page
can never hang the caller.

Handles HTML, PDF (pdftotext or pypdf) and plain text. Caches to disk so a
research loop doesn't hammer the same host twice.

  fetch.py https://example.com
  fetch.py https://a.com https://b.com --out pages/ --json
  fetch.py https://example.com --links --max-chars 8000
  fetch.py https://example.com --no-cache --json --quiet

Exit codes: 0 at least one page fetched · 2 all failed
"""
import argparse
import gzip
import html as html_lib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
CACHE_ROOT = os.environ.get("AGENT_SKILLS_CACHE") or os.path.expanduser(
    "~/.cache/agent-skills")
CACHE = os.path.join(CACHE_ROOT, "fetch")
# Bumped whenever the stored entry shape changes: an entry written by an older
# version is ignored instead of being misread (1.7.0 stopped storing the caller's
# truncated text, so 1.x entries must not be served).
CACHE_SCHEMA = 2
# A page body is capped before it is decoded or parsed. Without a cap one hostile
# URL is enough to exhaust the agent's memory: a 200 MB PDF, or a 1 MB gzip that
# inflates to 10 GB, costs nothing to serve.
MAX_BODY = 8 * 1024 * 1024              # bytes on the wire
MAX_INFLATED = 24 * 1024 * 1024         # bytes after gunzip
MAX_PDF = 48 * 1024 * 1024              # PDFs are legitimately large

try:                                    # the shared cache layer, vendored next to us
    from _cachekit import atomic_write_json as _atomic_write_json
    from _cachekit import nonneg_int, pos_float, pos_int
    from _cachekit import read_json as _read_json
except ImportError:                     # a lone copy of fetch.py: same behaviour
    def pos_int(text):                              # noqa: D103 - see _cachekit
        import argparse
        n = int(text)
        if n < 1:
            raise argparse.ArgumentTypeError(f"must be 1 or more (got {n})")
        return n

    def nonneg_int(text):                           # noqa: D103 - see _cachekit
        import argparse
        n = int(text)
        if n < 0:
            raise argparse.ArgumentTypeError(f"must be 0 or more (got {n})")
        return n

    def pos_float(text):                            # noqa: D103 - see _cachekit
        import argparse
        x = float(text)
        if not x > 0:
            raise argparse.ArgumentTypeError(f"must be greater than 0 (got {text})")
        return x

    def _atomic_write_json(path, obj, **kw):        # noqa: D103 - see _cachekit
        tmp = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
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
JINA = "https://r.jina.ai/"
# Free CORS relays: the page is fetched from *their* IP, so datacenter blocks and
# "enable JavaScript" walls often still serve us the HTML. Volunteers — flaky.
HTML_PROXIES = [
    ("allorigins", "https://api.allorigins.win/raw?url={}"),
    ("cors.lol", "https://api.cors.lol/?url={}"),
    ("codetabs", "https://api.codetabs.com/v1/proxy?quest={}"),
]
WAYBACK = "https://archive.org/wayback/available?url="
SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "template", "head",
             "nav", "footer", "aside", "form", "button", "select", "figure"}
BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "blockquote", "pre",
         "table", "ul", "ol", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6", "hr"}
BOILER = re.compile(
    r"(cookie|consent|subscribe|newsletter|sign in|log in|all rights reserved|"
    r"©\s?\d{4}|privacy policy|terms of service|accept all|advertisement|"
    r"we use cookies|javascript is disabled|enable javascript|skip to content|"
    r"share on (facebook|twitter|x|linkedin)|read more$)", re.I)


# ------------------------------------------------------------------ html reader
class Reader(HTMLParser):
    def __init__(self, keep_links=False):
        super().__init__(convert_charrefs=True)
        self.keep_links = keep_links
        self.stack, self.out, self.title, self.meta = [], [], [], {}
        self.links, self._href, self._hreftext = [], None, ""
        self._skip = 0
        self._in_pre = 0
        self._in_title = False
        self._buf = []

    # -- helpers
    def _flush(self):
        t = "".join(self._buf)
        self._buf = []
        return t

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in SKIP_TAGS:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "pre":
            self._in_pre += 1
        if tag in BLOCK:
            txt = self._flush().strip()
            if txt:
                self.out.append(txt)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append(f"\n{'#' * int(tag[1])} ")
        if tag == "li":
            self.out.append("- ")
        if tag == "a" and self.keep_links and a.get("href"):
            self._href, self._hreftext = a["href"], ""
        if tag == "meta":
            k = (a.get("property") or a.get("name") or "").lower()
            v = a.get("content") or ""
            if k and v and k in ("og:title", "og:description", "description",
                                 "author", "article:published_time", "og:site_name"):
                self.meta[k] = v[:500]

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag == "pre" and self._in_pre:
            self._in_pre -= 1
        if tag == "a" and self._href:
            if self._hreftext.strip():
                self.links.append((re.sub(r"\s+", " ", self._hreftext).strip()[:120], self._href))
            self._href, self._hreftext = None, ""
        if tag in BLOCK:
            txt = self._flush().strip()
            if txt:
                self.out.append(txt)

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title.append(data)
            return
        if self._href is not None:
            self._hreftext += data
        if self._in_pre:
            self._buf.append(data)
        else:
            self._buf.append(re.sub(r"[ \t\r\f\v]+", " ", data))

    def text(self):
        txt = self._flush().strip()
        if txt:
            self.out.append(txt)
        raw = " ".join(self.out)
        lines, seen_recent = [], []
        for ln in raw.split("\n"):
            ln = re.sub(r"[ \t]+", " ", ln).strip()
            if not ln:
                if lines and lines[-1] != "":
                    lines.append("")
                continue
            if len(ln) < 3 or BOILER.search(ln) and len(ln) < 200:
                continue
            if ln in seen_recent:
                continue
            seen_recent = (seen_recent + [ln])[-40:]
            if ln.startswith("#") or ln.startswith("- "):
                lines.append(ln)
            else:
                lines.append(ln)
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_markdown(html, keep_links=False, base=None):
    r = Reader(keep_links=keep_links)
    try:
        r.feed(html)
    except Exception:
        pass
    title = re.sub(r"\s+", " ", "".join(r.title)).strip() or r.meta.get("og:title", "")
    body = r.text()
    links = r.links
    if keep_links and base:
        # half the links on a normal page are relative; a caller that wants to
        # follow them needs absolute URLs or the whole hop-2 step is useless
        fixed = []
        for anchor, href in links:
            try:
                absu = urllib.parse.urljoin(base, href)
            except Exception:
                continue
            if absu.startswith(("http://", "https://")):
                fixed.append((anchor, absu))
        links = fixed
    return title, body, r.meta, links


# ------------------------------------------------------------------ transports
ALLOWED_SCHEMES = ("http", "https")


class BodyTooLarge(RuntimeError):
    """The response was bigger than we are willing to hold in memory."""


def _read_capped(resp, cap, what="body"):
    """Read at most *cap* bytes and refuse anything longer.

    read(cap + 1) asks the socket for one byte too many, which is how we tell
    "exactly cap" from "truncated to cap" without trusting Content-Length.
    """
    data = resp.read(cap + 1)
    if len(data) > cap:
        raise BodyTooLarge(f"{what} over {cap // 1048576} MB, refused")
    return data


def _gunzip_capped(raw, cap=None):
    """gunzip with a ceiling: a 1 MB bomb must not become 10 GB of text."""
    cap = MAX_INFLATED if cap is None else cap
    out = bytearray()
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        while True:
            chunk = gz.read(1 << 16)
            if not chunk:
                return bytes(out)
            out += chunk
            if len(out) > cap:
                raise BodyTooLarge(f"gunzipped body over {cap // 1048576} MB, refused")


def check_url(url):
    """Refuse anything that is not http(s).

    urllib will happily open `file:///etc/passwd` and `ftp://…`, and this tool
    hands whatever it gets to a language model. A page that says "now fetch
    file:///home/you/.ssh/id_rsa" must not be a working instruction, so the
    scheme is checked once, here, before any stage runs.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("empty URL")
    scheme = urllib.parse.urlsplit(url.strip()).scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(
            f"refusing scheme {scheme + ':' if scheme else '(none)'} — "
            f"only http and https are fetched")
    return url.strip()


def _open(url, timeout, headers=None, max_bytes=None):
    max_bytes = MAX_BODY if max_bytes is None else max_bytes
    check_url(url)                       # backstop: every stage goes through here
    h = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
         "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, identity"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = _read_capped(r, max_bytes, f"body from {urllib.parse.urlsplit(r.geturl()).netloc}")
        if (r.headers.get("Content-Encoding") or "").lower() == "gzip" or raw[:2] == b"\x1f\x8b":
            try:
                raw = _gunzip_capped(raw)
            except BodyTooLarge:
                raise
            except (OSError, EOFError, zlib.error):
                pass        # not gzip after all: keep the bytes we got
        return r.status, raw, dict(r.headers), r.geturl()


# ------------------------------------------------------- PDF, without a dependency
# pdftotext needs poppler, pypdf needs pip. On a bare Termux or a slim container
# both are missing, and the old chain fell straight through to a relay that is
# often blocked. This is a third option that needs nothing: inflate the content
# streams, read the text operators, and cope with the two things that usually
# turn PDF text into garbage — TeX ligature glyphs and subset-font encodings.
_PDF_ESC = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f",
            b"(": b"(", b")": b")", b"\\": b"\\"}

_GLYPHS = {
    "fi": "fi", "fl": "fl", "ff": "ff", "ffi": "ffi", "ffl": "ffl",
    "quoteright": "\u2019", "quoteleft": "\u2018", "quotedblleft": "\u201c",
    "quotedblright": "\u201d", "endash": "\u2013", "emdash": "\u2014",
    "bullet": "\u2022", "dotlessi": "\u0131", "hyphen": "-", "space": " ",
    "ellipsis": "\u2026", "section": "\u00a7", "minus": "\u2212",
    "multiply": "\u00d7", "degree": "\u00b0", "quotesingle": "'",
    "Lslash": "\u0141", "lslash": "\u0142", "OE": "\u0152", "oe": "\u0153",
    "AE": "\u00c6", "ae": "\u00e6", "Oslash": "\u00d8", "oslash": "\u00f8",
    "germandbls": "\u00df", "sterling": "\u00a3", "Euro": "\u20ac",
    "trademark": "\u2122", "registered": "\u00ae", "copyright": "\u00a9",
}
# LaTeX's T1/OT1 layout puts ff/fi/fl/ffi/ffl in the control range, where ASCII
# has nothing printable. Without this map "efficient" comes out as "e\x0ecient".
_DEFAULT_CTRL = {0x0b: "ff", 0x0c: "fi", 0x0d: "fl", 0x0e: "ffi", 0x0f: "ffl"}

_PDF_TOKEN = re.compile(rb"\((?:\\.|[^\\)])*\)|\[(?:\((?:\\.|[^\\)])*\)|[^\]])*\]"
                        rb"|<[0-9A-Fa-f\s]*>|-?\d+\.?\d*|[A-Za-z*'\"]{1,3}|BT|ET", re.S)


def _pdf_unescape(lit):
    out = bytearray()
    i = 0
    while i < len(lit):
        c = lit[i:i + 1]
        if c == b"\\" and i + 1 < len(lit):
            n = lit[i + 1:i + 2]
            if n in _PDF_ESC:
                out += _PDF_ESC[n]
                i += 2
                continue
            if n.isdigit():
                m = re.match(rb"[0-7]{1,3}", lit[i + 1:i + 4])
                if m:
                    out.append(int(m.group(0), 8) & 0xFF)
                    i += 1 + len(m.group(0))
                    continue
            if n in (b"\n", b"\r"):
                i += 2
                continue
            i += 1
            continue
        out += c
        i += 1
    return bytes(out)


def _pdf_ctrl_map(data, streams):
    """Code points < 32 that a /Differences array maps to real glyphs."""
    cmap = dict(_DEFAULT_CTRL)
    for src in [data] + [x for x in streams if b"/Differences" in x]:
        for m in re.finditer(rb"/Differences\s*\[(.*?)\]", src, re.S):
            code = 0
            for tok in re.findall(rb"(\d+)|/([A-Za-z0-9.]+)", m.group(1)):
                if tok[0]:
                    code = int(tok[0])
                    continue
                name = tok[1].decode("latin-1")
                if code < 32:
                    ch = _GLYPHS.get(name)
                    if ch is None and len(name) == 1:
                        ch = name
                    if ch is None and re.fullmatch(r"uni[0-9A-Fa-f]{4}", name):
                        ch = chr(int(name[3:], 16))
                    if ch:
                        cmap[code] = ch
                code += 1
    return cmap


def _pdf_decode_string(raw, cmap=None):
    if raw[:2] in (b"\xfe\xff", b"\xff\xfe"):
        try:
            return raw.decode("utf-16", "ignore")
        except Exception:
            return ""
    cmap = cmap or _DEFAULT_CTRL
    txt = "".join(cmap.get(b, chr(b)) if b < 32 else chr(b) for b in raw)
    # mostly unprintable means these are glyph ids from a CID font, not text
    if txt and sum(1 for ch in txt if ch.isprintable() or ch in "\n\t") / len(txt) < 0.7:
        return ""
    return txt


def pdf_extract_stdlib(data, max_bytes=30 * 1024 * 1024):
    """Best-effort text from PDF content streams. Handles FlateDecode, not CID fonts."""
    decoded = []
    for m in re.finditer(rb"(?<!end)stream\r?\n(.*?)\s*endstream", data[:max_bytes], re.S):
        raw = m.group(1)
        head = data[max(0, m.start() - 400):m.start()]
        if b"/FlateDecode" in head or raw[:1] in (b"x", b"\x78"):
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                try:
                    raw = zlib.decompressobj().decompress(raw)
                except zlib.error:
                    continue
        elif re.search(rb"/(?:DCTDecode|JPXDecode|CCITTFaxDecode|LZWDecode|ASCII85Decode"
                       rb"|RunLengthDecode|JBIG2Decode)", head):
            continue
        decoded.append(raw)
    cmap = _pdf_ctrl_map(data, decoded)
    texts = []
    for raw in decoded:
        if b"BT" not in raw or b"ET" not in raw:
            continue
        for block in re.findall(rb"BT(.*?)ET", raw, re.S):
            line, stack, last_y = [], [], None
            for tok in _PDF_TOKEN.finditer(block):
                t = tok.group(0)
                if t.startswith(b"(") or t.startswith(b"[") or t.startswith(b"<"):
                    stack.append(t)
                    continue
                if t in (b"Tj", b"'", b'"'):
                    if stack:
                        lit = stack[-1]
                        if lit.startswith(b"("):
                            line.append(_pdf_decode_string(_pdf_unescape(lit[1:-1]), cmap))
                        elif lit.startswith(b"<"):
                            try:
                                line.append(_pdf_decode_string(
                                    bytes.fromhex(re.sub(rb"\s", b"", lit[1:-1]).decode()), cmap))
                            except ValueError:
                                pass
                    if t != b"Tj":
                        line.append("\n")
                    stack = []
                elif t == b"TJ":
                    if stack and stack[-1].startswith(b"["):
                        for part in re.finditer(rb"\((?:\\.|[^\\)])*\)|(-?\d+\.?\d*)",
                                                stack[-1], re.S):
                            if part.group(1) is not None:
                                try:
                                    if float(part.group(1)) < -200:   # big kern = word gap
                                        line.append(" ")
                                except ValueError:
                                    pass
                            else:
                                line.append(_pdf_decode_string(
                                    _pdf_unescape(part.group(0)[1:-1]), cmap))
                    stack = []
                elif t in (b"T*", b"Td", b"TD", b"Tm"):
                    if t == b"T*":
                        line.append("\n")
                    elif t in (b"Td", b"TD") and len(stack) >= 2:
                        try:
                            dy = float(stack[-1])
                            if abs(dy) > 6:
                                line.append("\n")
                            elif float(stack[-2]) > 2 or abs(dy) > 0.5:
                                line.append(" ")
                        except ValueError:
                            pass
                    elif t == b"Tm" and len(stack) >= 6:
                        try:
                            y = float(stack[-1])
                            if last_y is not None and abs(y - last_y) > 6:
                                line.append("\n")
                            elif last_y is not None:
                                line.append(" ")
                            last_y = y
                        except ValueError:
                            pass
                    stack = []
                elif re.fullmatch(rb"-?\d+\.?\d*", t):
                    stack.append(t)
                else:
                    stack = []
            chunk = "".join(line)
            if chunk.strip():
                texts.append(chunk)
    out = "\n".join(texts)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    for a, b in (("\ufb00", "ff"), ("\ufb01", "fi"), ("\ufb02", "fl"),
                 ("\ufb03", "ffi"), ("\ufb04", "ffl")):
        out = out.replace(a, b)
    return out.strip()


def pdf_to_text(raw):
    """Each extractor is guarded on its own.

    They used to share one try block, which meant a pypdf that *raised* (a
    malformed file, a library quirk) took the built-in parser down with it and
    the call returned None — the fallback only worked when pypdf was absent
    entirely. CI caught it: the runner has pypdf installed, a dev box did not.
    """
    tmp = None
    try:
        if shutil.which("pdftotext"):
            import tempfile
            fd, tmp = tempfile.mkstemp(suffix=".pdf")
            os.write(fd, raw)
            os.close(fd)
            r = subprocess.run(["pdftotext", "-layout", tmp, "-"],
                               capture_output=True, text=True, timeout=120)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
    except Exception:
        pass
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(raw))
        txt = "\n\n".join((p.extract_text() or "") for p in reader.pages)
        if txt.strip():
            return txt
    except Exception:
        pass
    txt = pdf_extract_stdlib(raw)              # no poppler, no pypdf, still text
    if txt and len(txt.strip()) > 40:
        return txt
    return None


def fetch_direct(url, timeout, keep_links):
    t0 = time.time()
    status, raw, hdrs, final = _open(url, timeout, max_bytes=MAX_PDF)
    ctype = (hdrs.get("Content-Type") or "").lower()
    if "pdf" in ctype or url.lower().endswith(".pdf") or raw[:4] == b"%PDF":
        txt = pdf_to_text(raw)
        if not txt:
            raise RuntimeError("PDF but no text extractor (apt install poppler-utils)")
        return {"title": os.path.basename(urllib.parse.urlparse(url).path) or url,
                "text": re.sub(r"\n{3,}", "\n\n", txt), "meta": {"content_type": "pdf"},
                "links": [], "source": "direct", "final_url": final,
                "status": status, "elapsed": round(time.time() - t0, 2)}
    if "json" in ctype:
        try:
            return {"title": url, "text": json.dumps(json.loads(raw), indent=1)[:60000],
                    "meta": {"content_type": "json"}, "links": [], "source": "direct",
                    "final_url": final, "status": status, "elapsed": round(time.time() - t0, 2)}
        except Exception:
            pass
    enc = "utf-8"
    m = re.search(r"charset=([\w-]+)", ctype)
    if m:
        enc = m.group(1)
    html = raw.decode(enc, "replace")
    title, body, meta, links = html_to_markdown(html, keep_links, final)
    if len(body) < 200:
        raise RuntimeError(f"thin content ({len(body)} chars) — probably JS-rendered")
    return {"title": title or url, "text": body, "meta": meta, "links": links,
            "source": "direct", "final_url": final, "status": status,
            "elapsed": round(time.time() - t0, 2)}


# ------------------------------------------------------------------ rewrites
# Some URLs have a machine-readable twin: one request, no navigation sandwich,
# no bot wall. Two of these are unconditionally better than the page itself
# (a github blob is the same file with a UI around it; a StackOverflow question
# is the same Q&A with a Cloudflare wall in front of it), so they run *before*
# the direct stage. The rest run after the relays have failed.
REWRITE_PRIORITY = ("reddit", "x-post", "gh-raw", "stackexchange")
SE_SITES = ("stackoverflow.com", "superuser.com", "serverfault.com", "askubuntu.com")


def rewrite_candidates(url):
    """[(name, target, kind)] for this URL; kind is "raw" | "json" | "html".

    Pure function — no network, so the mapping is tested directly.
    """
    p = urllib.parse.urlparse(url)
    host = (p.netloc or "").lower()
    host = host[4:] if host.startswith("www.") else host
    path, query = p.path or "/", p.query
    out = []

    if reddit_target(url):
        out.append(("reddit", url, "reddit"))
    if x_target(url):
        out.append(("x-post", url, "x"))

    if host == "github.com" and "/blob/" in path:
        out.append(("gh-raw", "https://raw.githubusercontent.com" + path.replace("/blob/", "/", 1), "raw"))

    se = host in SE_SITES or host.endswith(".stackexchange.com")
    if se:
        m = re.match(r"/questions/(\d+)", path)
        if m:
            site = ("stackoverflow" if "stackoverflow" in host
                    else host.split(".")[0])
            out.append(("stackexchange",
                        f"https://api.stackexchange.com/2.3/questions/{m.group(1)}"
                        f"?site={site}&filter=withbody", "json"))

    if host.endswith("wikipedia.org") and path.startswith("/wiki/"):
        lang = host.split(".")[0]
        title = urllib.parse.quote(path[len("/wiki/"):])
        out.append(("wikipedia-summary",
                    f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}", "json"))

    if host in ("youtube.com", "m.youtube.com") and path.rstrip("/") == "/watch":
        vid = urllib.parse.parse_qs(query).get("v", [None])[0]
        if vid:
            out.append(("youtube-oembed",
                        "https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v=" + vid,
                        "json"))
    elif host == "youtu.be" and path.strip("/"):
        out.append(("youtube-oembed",
                    "https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v="
                    + path.strip("/"), "json"))

    if host == "medium.com" or host.endswith(".medium.com"):
        out.append(("scribe", "https://scribe.rip" + path, "html"))

    if host in ("x.com", "twitter.com", "mobile.twitter.com"):
        for n, mirror in (("nitter", "nitter.net"),
                          ("nitter-privacydev", "nitter.privacydev.net"),
                          ("xcancel", "xcancel.com")):
            out.append((n, f"https://{mirror}{path}", "html"))

    if host == "arxiv.org" and path.startswith("/abs/"):
        out.append(("arxiv-pdf", "https://arxiv.org/pdf/" + path[len("/abs/"):], "html"))
    return out


def order_rewrites(cands):
    """(before, after) — priority rewrites first, the rest after the relays."""
    before = [c for c in cands if c[0] in REWRITE_PRIORITY]
    after = [c for c in cands if c[0] not in REWRITE_PRIORITY]
    return before, after


# ------------------------------------------------------------------ wall check
# A relay answering 200 with a bot wall or a signup gate is not an answer. This
# is how fetch.py once returned ok:true with 20,000 characters of StackOverflow's
# signup page: every stage "succeeded" and none of them had read the article.
WALL_CHALLENGE = ("just a moment", "checking your browser", "verifying your browser",
                  "attention required", "unusual traffic", "are you a robot",
                  "verify you are human", "enable javascript and cookies",
                  "please enable javascript", "access denied", "cf-error-details",
                  "warning: target url returned error")
WALL_SIGNUP = ("sign up with google", "sign up with github", "continue with google",
               "join stack overflow", "log in to continue", "sign in to continue",
               "subscribe to continue", "for subscribers only", "become a member to read",
               "create a free account", "create your account to continue")


def wall_reason(text):
    """Name the wall this body is, or None if it looks like a page."""
    head = (text or "")[:700].lower()
    body = (text or "")[:3000].lower()
    for m in WALL_SIGNUP:
        if m in head:
            return m
    for m in WALL_CHALLENGE:
        if m in body:
            return m
    return None


# ----------------------------------------------------------- rewrites, fetching
def _render_stackexchange(js):
    items = (js or {}).get("items") or []
    if not items:
        raise RuntimeError("stackexchange: no items")
    q = items[0]

    def frag(h):
        return (html_to_markdown(h or "", False, "")[1] or "").strip()

    title = html_lib.unescape(re.sub(r"<[^>]+>", "", q.get("title") or "")).strip() or "question"
    bits = [f"{q.get('score', 0)} votes"]
    if q.get("answer_count") is not None:
        bits.append(f"{q['answer_count']} answers")
    if q.get("is_answered"):
        bits.append("has an accepted answer")
    parts = [f"# {title}", f"_{' · '.join(bits)}_ · {q.get('link', '')}", frag(q.get("body"))]
    answers = [x for x in items if x.get("answer_id")][:3]
    for i, a in enumerate(answers, 1):
        head = f"## Answer {i}"
        if a.get("is_accepted"):
            head += " (accepted)"
        owner = (a.get("owner") or {}).get("display_name") or ""
        head += f" — {a.get('score', 0)} votes" + (f", {owner}" if owner else "")
        parts += [head, frag(a.get("body"))]
    return title, "\n\n".join(p for p in parts if p), {"content_type": "markdown"}


def _render_wikipedia(js):
    title = (js or {}).get("title") or ""
    extract = ((js or {}).get("extract") or "").strip()
    if not extract:
        raise RuntimeError("wikipedia: no extract")
    body = (f"# {title}\n\n{extract}\n\n---\n\n"
            "_Wikipedia REST summary — the lede only; fetch the article page for the full text._")
    return title or "summary", body, {"content_type": "markdown", "summary": True}


def _render_youtube(js):
    js = js or {}
    title = js.get("title") or ""
    author = js.get("author_name") or ""
    body = (f"# {title}\n\n" + (f"by {author}\n\n" if author else "")
            + "_YouTube oEmbed metadata — the title and channel, not the video._")
    return title or "video", body, {"content_type": "markdown", "summary": True}


_JSON_RENDERERS = {"stackexchange": _render_stackexchange,
                   "wikipedia-summary": _render_wikipedia,
                   "youtube-oembed": _render_youtube}


def fetch_rewrite(name, target, kind, timeout, keep_links=False):
    """Fetch a machine-readable twin of *target* and label it rewrite:<name>."""
    if kind == "reddit":
        return fetch_reddit(target, timeout, keep_links)
    if kind == "x":
        return fetch_x(target, timeout, keep_links)
    t0 = time.time()
    if kind == "html":
        res = dict(fetch_direct(target, timeout, keep_links))
    else:
        status, raw, hdrs, final = _open(target, timeout)
        body = raw.decode("utf-8", "replace")
        if kind == "raw":
            text = body
            title = os.path.basename(urllib.parse.urlparse(target).path) or target
            meta = {"content_type": "text"}
        else:
            render = _JSON_RENDERERS.get(name)
            if render is None:
                raise RuntimeError(f"rewrite:{name}: no renderer")
            title, text, meta = render(json.loads(body))
        res = {"title": title, "text": text, "meta": meta, "links": [],
               "final_url": target, "status": status}
    res["source"] = f"rewrite:{name}"
    res["elapsed"] = round(time.time() - t0, 2)
    if not (res.get("text") or "").strip():
        raise RuntimeError(f"rewrite:{name}: empty")
    return res


_JINA_STATE = {"dead": False, "why": ""}


def _reader_budget_sleep(i):
    time.sleep(0.8 * (i + 1))


def fetch_reader(url, timeout, keep_links=False):
    """Stage 2. Two independent relays, because either can vanish:

      a) r.jina.ai  — renders the page (JS too) from its own IP, returns markdown
      b) CORS proxy — relays the raw HTML, which we convert ourselves

    The reader is a third-party service; when it starts answering 401/429 to
    datacenter IPs we mark it dead for the run and go straight to the proxies.
    """
    errors = []
    # (a) markdown reader
    if not _JINA_STATE["dead"]:
        try:
            t0 = time.time()
            status, raw, hdrs, final = _open(JINA + url, max(timeout, 40),
                                             headers={"Accept": "text/plain"})
            txt = raw.decode("utf-8", "replace")
            if status == 200 and len(txt) >= 100:
                title = url
                m = re.search(r"^Title:\s*(.+)$", txt, re.M)
                if m:
                    title = m.group(1).strip()
                body = re.sub(r"^(Title|URL Source|Markdown Content):.*\n", "", txt,
                              flags=re.M).strip()
                if len(body) >= 200:
                    return {"title": title, "text": body,
                            "meta": {"content_type": "markdown"}, "links": [],
                            "source": "reader:jina", "final_url": url, "status": status,
                            "elapsed": round(time.time() - t0, 2)}
                errors.append(f"reader:jina thin ({len(body)}B)")
            else:
                errors.append(f"reader:jina {status}/{len(txt)}B")
                if status in (401, 402, 403, 429):
                    _JINA_STATE["dead"], _JINA_STATE["why"] = True, f"HTTP {status}"
        except urllib.error.HTTPError as e:
            errors.append(f"reader:jina HTTP {e.code}")
            if e.code in (401, 402, 403, 429):
                _JINA_STATE["dead"], _JINA_STATE["why"] = True, f"HTTP {e.code}"
        except Exception as e:
            errors.append(f"reader:jina {str(e)[:60]}")

    # (b) CORS relay of the raw HTML
    for i, (name, tpl) in enumerate(HTML_PROXIES):
        try:
            t0 = time.time()
            status, raw, hdrs, final = _open(tpl.format(urllib.parse.quote(url, safe="")),
                                            timeout)
            ctype = (hdrs.get("Content-Type") or "").lower()
            if "pdf" in ctype or raw[:4] == b"%PDF":
                txt = pdf_to_text(raw)
                if txt:
                    return {"title": os.path.basename(urllib.parse.urlparse(url).path) or url,
                            "text": re.sub(r"\n{3,}", "\n\n", txt),
                            "meta": {"content_type": "pdf"}, "links": [],
                            "source": f"reader:{name}", "final_url": url,
                            "status": status, "elapsed": round(time.time() - t0, 2)}
            html = raw.decode("utf-8", "replace")
            title, body, meta, links = html_to_markdown(html, keep_links, url)
            if len(body) >= 200:
                return {"title": title or url, "text": body, "meta": meta,
                        "links": links[:200], "source": f"reader:{name}",
                        "final_url": url, "status": status,
                        "elapsed": round(time.time() - t0, 2)}
            errors.append(f"reader:{name} thin ({len(body)}B)")
        except urllib.error.HTTPError as e:
            errors.append(f"reader:{name} HTTP {e.code}")
        except Exception as e:
            errors.append(f"reader:{name} {str(e)[:60]}")
        _reader_budget_sleep(i)
    raise RuntimeError("; ".join(errors[-3:]) or "reader exhausted")


def fetch_archive(url, timeout, keep_links):
    t0 = time.time()
    _, raw, _, _ = _open(WAYBACK + urllib.parse.quote(url, safe=""), timeout)
    js = json.loads(raw.decode("utf-8", "replace"))
    snap = (js.get("archived_snapshots") or {}).get("closest") or {}
    if not snap.get("url"):
        raise RuntimeError("no Wayback snapshot")
    res = fetch_direct(snap["url"], timeout, keep_links)
    res["source"] = "wayback"
    res["final_url"] = snap["url"]
    res["elapsed"] = round(time.time() - t0, 2)
    res["snapshot_ts"] = snap.get("timestamp")
    return res


# ----------------------------------------------------------------------- cache
VENDOR_UA = "keyless-agent-skills/1.3 (+https://github.com/aminmzadev/keyless-agent-skills)"
FIRECRAWL_SCRAPE = "https://api.firecrawl.dev/v2/scrape"


def _vendor_endpoint():
    """Override for tests and for anyone running a self-hosted Firecrawl."""
    return os.environ.get("FIRECRAWL_SCRAPE_URL", FIRECRAWL_SCRAPE)


def fetch_vendor(url, timeout, keep_links=False):
    """Stage 3. Firecrawl's public scrape endpoint, called with no credential.

    This is the stage that earns its place: it renders JS and gets past bot
    walls that stop the reader relays, so pages Medium and Cloudflare hand back
    a 403 to a plain HTTP client still come out as markdown here.

    It is deliberately *not* first. It is borrowed infrastructure with no SLA,
    and the direct path costs the vendor nothing.

    Disable with --no-vendor or FETCH_VENDOR=0.
    """
    t0 = time.time()
    body = json.dumps({"url": url, "formats": ["markdown"]}).encode()
    req = urllib.request.Request(
        _vendor_endpoint(), data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": VENDOR_UA,
                 "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode("utf-8", "replace"))
    data = payload.get("data") or {}
    text = data.get("markdown") or data.get("html") or ""
    if not text.strip():
        raise RuntimeError("vendor: empty markdown")
    meta = data.get("metadata") or {}
    return {"title": meta.get("title") or url, "text": text,
            "meta": {"content_type": "markdown", "vendor": "firecrawl",
                     "status_code": meta.get("statusCode")},
            "links": [], "source": "vendor:firecrawl",
            "final_url": meta.get("url") or url,
            "status": meta.get("statusCode") or 200,
            "elapsed": round(time.time() - t0, 2)}


EXA_MCP = "https://mcp.exa.ai/mcp"
EXA_MAX_CHARS = 50000          # what we ask for; the caller's max_chars still wins


def _exa_endpoint():
    return os.environ.get("EXA_MCP_URL", EXA_MCP)


def _sse_json(raw):
    """Exa's MCP answers either plain JSON or server-sent events. Handle both."""
    raw = (raw or "").strip()
    if raw.startswith("{"):
        return json.loads(raw)
    data = "\n".join(ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:"))
    if not data:
        raise RuntimeError("exa: empty body")
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return json.loads("".join(data.split()))


def fetch_exa(url, timeout, keep_links=False):
    """Stage 4. Exa's anonymous MCP `web_fetch_exa` tool.

    Kept behind Wayback because it is the second borrower in the chain and
    Wayback is a public archive with an explicit no-SLA promise of its own.
    Ahead of it because when both answer, Exa returns the page's own markdown
    while the archive returns whatever snapshot it happens to hold.

    maxCharacters matters: left unset, this endpoint returns ~3,000 characters
    and looks like a hard cap. Asking for 50,000 returned 31,442 on a long
    Wikipedia article, so the real ceiling is on their side, not ours.

    Disable with --no-vendor or FETCH_VENDOR=0, like the Firecrawl stage.
    """
    t0 = time.time()
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "web_fetch_exa",
                   "arguments": {"urls": [url], "maxCharacters": EXA_MAX_CHARS}},
    }).encode()
    req = urllib.request.Request(
        _exa_endpoint(), data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": VENDOR_UA,
                 "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = _sse_json(r.read().decode("utf-8", "replace"))
    if isinstance(payload, dict) and payload.get("error"):
        err = payload["error"]
        raise RuntimeError("exa: " + str((err or {}).get("message") or err)[:110])
    result = (payload or {}).get("result") or {}
    parts = [c.get("text") or "" for c in (result.get("content") or [])
             if isinstance(c, dict)]
    text = "\n".join(p for p in parts if p.strip())
    if not text.strip():
        raise RuntimeError("exa: empty result")
    # drop Exa's own "URL:"/"Published:" preamble, keep the page
    lines = [ln for ln in text.splitlines()
             if not ln.startswith(("URL: http", "Published: "))]
    text = "\n".join(lines).strip()
    title = url
    for ln in lines:
        if ln.startswith("# "):
            title = ln[2:].strip() or url
            break
    return {"title": title, "text": text,
            "meta": {"content_type": "markdown", "vendor": "exa", "status_code": 200},
            "links": [], "source": "vendor:exa", "final_url": url, "status": 200,
            "elapsed": round(time.time() - t0, 2)}


# ------------------------------------------------- reddit, without reddit.com
# reddit.com 403s every plain client from a datacenter IP (old.reddit 403, .json
# 403, /s/ share links 403) and every redlib mirror is behind Cloudflare or rate
# limited. The archive API is not: arctic-shift serves full post and comment
# objects, keyless, and includes the gallery metadata that carries the image
# URLs. Images themselves live on i.redd.it, which answers plain HTTP fine.
ARCTIC = "https://arctic-shift.photon-reddit.com"
X_API = "https://api.fxtwitter.com"
REDDIT_COMMENTS = 20


def _arctic():
    return os.environ.get("ARCTIC_SHIFT_URL", ARCTIC)


def _x_api():
    return os.environ.get("FXTWITTER_URL", X_API)


def reddit_target(url):
    """(kind, key, extra) for a reddit URL, or None. kind: "post" | "listing"."""
    p = urllib.parse.urlparse(url)
    host = (p.netloc or "").lower()
    host = host[4:] if host.startswith("www.") else host
    path = p.path or "/"
    if host not in ("reddit.com", "old.reddit.com", "new.reddit.com", "np.reddit.com",
                    "redd.it", "sh.reddit.com"):
        if not host.endswith(".reddit.com"):
            return None
    m = re.match(r"^/([a-z0-9]{4,10})/?$", path, re.I)    # redd.it/<id>
    if m and host == "redd.it":
        return ("post", m.group(1), {})
    m = re.search(r"/comments/([a-z0-9]{4,10})(?:/|$)", path, re.I)
    if m:
        return ("post", m.group(1).lower(), {})
    m = re.search(r"/gallery/([a-z0-9]{4,10})", path, re.I)
    if m:
        return ("post", m.group(1).lower(), {})
    if "/s/" in path:
        return None                                        # share links 403 for everyone
    m = re.match(r"^/r/([A-Za-z0-9_+]{2,50})(?:/(hot|new|top|rising|controversial))?/?$", path)
    if m:
        return ("listing", m.group(1), {"sort": "asc" if (m.group(2) or "") == "new" else "desc"})
    m = re.match(r"^/(?:u|user)/([A-Za-z0-9_-]{3,30})/?$", path)
    if m:
        return ("listing", m.group(1), {"author": True})
    return None


def x_target(url):
    """(user, status_id) for an x.com / twitter.com status URL, or None."""
    p = urllib.parse.urlparse(url)
    host = (p.netloc or "").lower()
    host = host[4:] if host.startswith("www.") else host
    if host not in ("x.com", "twitter.com", "mobile.twitter.com", "mobile.x.com", "t.co"):
        return None
    m = re.search(r"/status(?:es)?/(\d{1,25})", p.path or "")
    if m:
        return (None, m.group(1))
    return None


def _reddit_media(p):
    """Every image/video URL a post carries, in gallery order, deduplicated."""
    out, seen = [], set()

    def add(u, kind="image", w=None, h=None):
        if not u:
            return
        u = html_lib.unescape(u)
        key = urllib.parse.urlsplit(u)._replace(query="").geturl() if "preview.redd.it" not in u else u
        if key in seen:
            return
        seen.add(key)
        out.append({"url": u, "type": kind, "width": w, "height": h})

    md = p.get("media_metadata") if isinstance(p.get("media_metadata"), dict) else {}
    if md:
        order = [i.get("media_id") for i in ((p.get("gallery_data") or {}).get("items") or [])]
        for mid in [i for i in order if i in md] + [k for k in md if k not in order]:
            m = md.get(mid) or {}
            src = m.get("s") or {}
            add(src.get("u") or src.get("gif"), "image", src.get("x"), src.get("y"))

    url = p.get("url_overridden_by_dest") or p.get("url") or ""
    if re.search(r"\.(?:jpe?g|png|gif|webp|avif)(?:\?|$)", url, re.I) or \
            re.search(r"^https?://(i\.redd\.it|i\.imgur\.com|preview\.redd\.it)/", url):
        add(url, "image")

    rv = ((p.get("media") or {}).get("reddit_video") or {})
    if p.get("is_video") and rv.get("fallback_url"):
        add(rv.get("fallback_url"), "video", rv.get("width"), rv.get("height"))

    if not out:
        try:
            src = p["preview"]["images"][0]["source"]
            add(src.get("url"), "image", src.get("width"), src.get("height"))
        except Exception:
            pass
    return out


def _reddit_stamp(p):
    try:
        return datetime.fromtimestamp(float(p.get("created_utc") or 0), timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return "?"


def _render_reddit_post(post, comments, comments_total):
    title = html_lib.unescape((post.get("title") or "").strip()) or "(no title)"
    bits = [f"r/{post.get('subreddit') or '?'}", f"u/{post.get('author') or '[deleted]'}",
            f"{post.get('score', 0)} points", f"{post.get('num_comments', 0)} comments",
            _reddit_stamp(post)]
    if post.get("link_flair_text"):
        bits.insert(0, str(post["link_flair_text"]))
    if post.get("over_18"):
        bits.append("NSFW")
    parts = [f"# {title}", "_" + " · ".join(bits) + "_"]

    selftext = (post.get("selftext") or "").strip()
    if selftext and selftext not in ("[removed]", "[deleted]"):
        parts.append(selftext)
    elif post.get("is_self") is False:
        pass
    elif selftext:
        parts.append(f"_{selftext}_")

    media = _reddit_media(post)
    if media:
        lines = ["## Media", ""]
        for i, m in enumerate(media, 1):
            dim = f" ({m['width']}×{m['height']})" if m.get("width") and m.get("height") else ""
            lines.append(f"{i}. {m['type']}{dim}: {m['url']}")
        parts.append("\n".join(lines))
    elif post.get("url") and not post.get("is_self"):
        parts.append(f"Link: {post['url']}")

    if comments:
        lines = [f"## Comments (top {len(comments)} of {comments_total or len(comments)})", ""]
        for c in comments:
            body = (c.get("body") or "").strip()
            if not body:
                continue
            lines.append(f"**u/{c.get('author') or '[deleted]'}** · {c.get('score', 0)} points")
            lines.append("")
            lines.append(body)
            lines.append("")
            lines.append("---")
            lines.append("")
        parts.append("\n".join(lines).rstrip("-\n "))
    return title, "\n\n".join(p for p in parts if p)


def fetch_reddit(url, timeout, keep_links=False):
    """Stage 0 for reddit URLs: post or listing, with pictures, no reddit.com."""
    tgt = reddit_target(url)
    if not tgt:
        raise RuntimeError("reddit: not a post, subreddit or user URL (share links /s/ are not resolvable)")
    kind, key, extra = tgt
    t0 = time.time()

    if kind == "post":
        status, raw, hdrs, final = _open(f"{_arctic()}/api/posts/ids?ids={key}", timeout)
        data = (json.loads(raw.decode("utf-8", "replace")) or {}).get("data") or []
        if not data:
            raise RuntimeError(f"reddit: no post with id {key} in the archive")
        post = data[0]
        comments = []
        try:
            cstatus, craw, _, _ = _open(
                f"{_arctic()}/api/comments/search?link_id={key}&limit={REDDIT_COMMENTS * 4}", timeout)
            items = (json.loads(craw.decode("utf-8", "replace")) or {}).get("data") or []
            items = [c for c in items if (c.get("body") or "").strip()
                     and c.get("body") not in ("[removed]", "[deleted]")]
            items.sort(key=lambda c: (c.get("score") or 0), reverse=True)
            comments = items[:REDDIT_COMMENTS]
        except Exception:
            pass                      # a post without comments is still a post
        title, text = _render_reddit_post(post, comments, post.get("num_comments"))
        media = _reddit_media(post)
        source = "reddit:post"
    else:
        q = {"limit": "25", "sort": extra.get("sort", "desc")}
        if extra.get("author"):
            q["author"] = key
        else:
            q["subreddit"] = key
        status, raw, hdrs, final = _open(
            f"{_arctic()}/api/posts/search?" + urllib.parse.urlencode(q), timeout)
        posts = (json.loads(raw.decode("utf-8", "replace")) or {}).get("data") or []
        if not posts:
            raise RuntimeError(f"reddit: no posts for {key}")
        head = f"r/{key}" if not extra.get("author") else f"u/{key}"
        lines = [f"# {head}", f"_{len(posts)} posts from the archive, newest first_", ""]
        media = []
        for i, post in enumerate(posts, 1):
            title = html_lib.unescape((post.get("title") or "(no title)").strip())
            lines.append(f"{i}. **{title}** — u/{post.get('author') or '[deleted]'}, "
                         f"{post.get('score', 0)} points, {post.get('num_comments', 0)} comments, "
                         f"{_reddit_stamp(post)}")
            lines.append(f"   https://www.reddit.com{post.get('permalink') or ''}")
            m = _reddit_media(post)
            media.extend(m[:3])
            for item in m[:3]:
                lines.append(f"   {item['type']}: {item['url']}")
        text = "\n".join(lines)
        title = head
        source = "reddit:listing"

    return {"title": title, "text": text,
            "meta": {"content_type": "markdown", "forum": "reddit", "kind": kind},
            "links": [], "source": source, "final_url": url,
            "status": 200, "elapsed": round(time.time() - t0, 2), "media": media}


# ------------------------------------------------------------ x, without x.com
# x.com needs a logged-in session and its API needs a bearer token. The public
# fixer APIs do not: fxtwitter returns the tweet as JSON, including every photo
# (pbs.twimg.com) and video (video.twimg.com) URL at full resolution. Those CDNs
# are public and answer plain HTTP.
def _render_x_tweet(t):
    a = t.get("author") or {}
    name = a.get("name") or ""
    handle = a.get("screen_name") or ""
    bits = []
    if t.get("created_at"):
        bits.append(str(t["created_at"]).replace(" +0000", " UTC"))
    for k, label in (("likes", "likes"), ("reposts", "reposts"), ("replies", "replies"),
                     ("views", "views")):
        if t.get(k):
            bits.append(f"{t[k]} {label}")
    parts = [f"# {name} (@{handle})".strip(), "_" + " · ".join(bits) + "_" if bits else "",
             (t.get("text") or "").strip()]

    media, lines = [], []
    m = t.get("media") or {}
    for ph in (m.get("photos") or []):
        url = ph.get("url") or ""
        media.append({"url": url, "type": "image", "width": ph.get("width"), "height": ph.get("height")})
        lines.append(f"- image{_dim(ph)}: {url}")
    for v in (m.get("videos") or []) + (m.get("gifs") or []):
        url = v.get("url") or ""
        kind = "gif" if v in (m.get("gifs") or []) else "video"
        media.append({"url": url, "type": kind, "width": v.get("width"), "height": v.get("height"),
                      "duration": v.get("duration")})
        lines.append(f"- {kind}{_dim(v)}" + (f" {v['duration']:.0f}s" if v.get("duration") else "")
                     + f": {url}")
    if lines:
        parts += ["## Media", "\n".join(lines)]

    q = t.get("quote")
    if isinstance(q, dict) and q.get("text"):
        qa = q.get("author") or {}
        parts += ["## Quoted", f"**@{qa.get('screen_name', '?')}**: {q['text'].strip()}"]
    if t.get("possibly_sensitive"):
        parts.insert(1, "_marked sensitive by the author_")
    return " ".join(x for x in [name, f"@{handle}"] if x) or "tweet", "\n\n".join(p for p in parts if p), media


def _dim(x):
    if x.get("width") and x.get("height"):
        return f" ({x['width']}×{x['height']})"
    return ""


def fetch_x(url, timeout, keep_links=False):
    """Stage 0 for x.com/twitter.com status URLs: text, photos, videos."""
    tgt = x_target(url)
    if not tgt:
        raise RuntimeError("x: only /status/<id> URLs are fetchable without a session")
    _user, sid = tgt
    t0 = time.time()
    status, raw, hdrs, final = _open(f"{_x_api()}/status/{sid}", timeout)
    payload = json.loads(raw.decode("utf-8", "replace")) or {}
    if payload.get("code") not in (200, None) or not payload.get("tweet"):
        raise RuntimeError(f"x: {payload.get('message') or 'no tweet ' + sid}")
    title, text, media = _render_x_tweet(payload["tweet"])
    return {"title": title, "text": text,
            "meta": {"content_type": "markdown", "social": "x", "tweet_id": sid},
            "links": [], "source": "x:post", "final_url": url,
            "status": 200, "elapsed": round(time.time() - t0, 2), "media": media}


_MAGIC = ((b"\xff\xd8\xff", "jpeg"), (b"\x89PNG", "png"), (b"GIF8", "gif"),
          (b"\x1aE\xdf\xa3", "webm"), (b"%PDF", "pdf"))


def media_kind(head, ctype=""):
    """What this body actually is, by magic bytes first and Content-Type second."""
    for sig, kind in _MAGIC:
        if head.startswith(sig):
            return kind
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp":
        return "mp4" if b"avi" not in head[8:12] else "avif"
    ctype = (ctype or "").split(";")[0].strip().lower()
    for pre, kind in (("image/", ctype[6:]), ("video/", ctype[6:])):
        if ctype.startswith(pre):
            return kind.split("+")[0] or "bin"
    if ctype == "application/octet-stream":
        return "bin"
    return ""


def next_media_index(outdir):
    """First free NNN- slot in *outdir*, so two calls cannot write the same file."""
    best = 0
    try:
        names = os.listdir(outdir)
    except OSError:
        return 1
    for name in names:
        head = name.split("-", 1)[0]
        if head.isdigit():
            best = max(best, int(head))
    return best + 1


def _media_name(n, url_path, ext):
    base = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(url_path)[:40] or "media")
    name = f"{n:02d}-{base}"
    if not name.lower().endswith(ext):
        name += ext
    return name


def download_media(items, outdir, timeout=30, limit=25, max_bytes=40 * 1024 * 1024):
    """Save photos and videos next to the markdown. Returns the paths written.

    A CDN answering 200 with an HTML error page used to be written out as
    `03-media.mp4`: valid-looking file, no video, and it counted against the
    cap. Now the bytes have to look like media before they are kept.
    """
    os.makedirs(outdir, exist_ok=True)
    written = []
    n = next_media_index(outdir)
    for it in (items or []):
        if len(written) >= limit:
            break
        url = it.get("url") or ""
        if not url.startswith("http"):
            continue
        path = urllib.parse.urlsplit(url).path
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".mp4", ".webm"):
            ext = ".mp4" if it.get("type") == "video" else ".jpg"
        # `fetch.py a b c --media DIR` must not restart at 01- for every URL and
        # quietly overwrite the previous page's pictures
        dest = os.path.join(outdir, _media_name(n, path, ext))
        while os.path.exists(dest):
            n += 1
            dest = os.path.join(outdir, _media_name(n, path, ext))
        n += 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                ctype = r.headers.get("Content-Type") or ""
                data = r.read(max_bytes + 1)
            if len(data) > max_bytes:
                continue
            if not media_kind(data[:16], ctype):
                continue            # an HTML error page is not a picture
            with open(dest, "wb") as fh:
                fh.write(data)
            written.append(dest)
        except Exception:
            continue
    return written


class StageTimeout(RuntimeError):
    """A stage ignored its own socket timeouts and had to be abandoned."""


def _run_with_budget(fn, budget, name="stage"):
    """Run fn() but never wait longer than *budget* seconds for it.

    A daemon thread is the only way to bound a stage that blocks inside a
    socket read or a DNS lookup: socket timeouts do not cover every hop, and a
    threading future's timeout would still leave the thread alive and keep the
    interpreter from exiting.
    """
    box = {}

    def runner():
        try:
            box["value"] = fn()
        except BaseException as exc:      # noqa: BLE001 - re-raised on the caller side
            box["error"] = exc

    worker = threading.Thread(target=runner, daemon=True, name=f"fetch-{name}")
    worker.start()
    worker.join(max(0.1, budget))
    if worker.is_alive():
        raise StageTimeout(f"no answer within {budget:.0f}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _vendor_enabled(flag):
    if not flag:
        return False
    return os.environ.get("FETCH_VENDOR", "1").strip().lower() not in ("0", "false", "no", "off")


def cache_path(url):
    return os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + ".json")


def cache_get(url, ttl):
    """Return the stored entry for *url* as stored: full text, caller-shaped later."""
    if not ttl:
        return None
    d = _read_json(cache_path(url))
    if not isinstance(d, dict) or d.get("_schema") != CACHE_SCHEMA:
        return None
    if time.time() - d.get("_ts", 0) <= ttl:
        d["cached"] = True
        d["source"] = d.get("source", "cache")
        return d
    return None


def cache_put(url, data):
    """Store *data* verbatim. Never store a caller's view of it: see _shape()."""
    d = dict(data)
    d["_ts"] = time.time()
    d["_schema"] = CACHE_SCHEMA
    try:
        _atomic_write_json(cache_path(url), d)
    except OSError:
        pass        # a cache we cannot write must never fail the fetch


def _shape(res, max_chars):
    """Turn a fetched/cached result into what the caller asked for.

    The cache keeps the *whole* text; the cap belongs to the caller, not to the
    file. Truncating before storing meant a later call with a larger --max-chars
    silently received the smaller earlier slice, and reported it as the page's
    length. `full_chars` lets a caller notice that it is looking at a slice.
    """
    out = dict(res)
    full = res.get("text") or ""
    out["text"] = full[:max_chars]
    out["chars"] = len(out["text"])
    out["full_chars"] = len(full)
    out.setdefault("media", [])
    return out


def _via_browser(url, max_chars=20000, wait=4.0):
    """Local patch: read the page through Minis's own WebView.

    minis-browser-use drives the in-app WebView from the shell. It is the only
    transport on this device that renders JS and survives bot walls, so it is used
    as the last stage instead of failing outright. Returns a result dict shaped
    like every other stage, or None.
    """
    import json as _json
    import shutil as _shutil
    import subprocess as _sp

    exe = _shutil.which("minis-browser-use")
    if not exe:
        return None
    _started = time.monotonic()

    def call(action, extra=(), tmo=90):
        try:
            p = _sp.run([exe, action, *extra], capture_output=True, text=True, timeout=tmo)
        except (subprocess.TimeoutExpired, OSError):
            return None
        blob = p.stdout or ""
        start = blob.find("{")
        if start < 0:
            return None
        try:
            return _json.loads(blob[start:blob.rfind("}") + 1])
        except ValueError:
            return None

    if not (call("navigate", ["--url", url]) or {}).get("ok"):
        return None
    time.sleep(max(1.0, wait))
    for _ in range(3):
        env = call("get_readable")
        if env and env.get("ok"):
            text = (env.get("data") or {}).get("text") or ""
            m = re.search(r"\((\d+)\s*chars\)", text)
            if m and int(m.group(1)) > 200:
                title = text.split("\n", 1)[0]
                title = title.replace("Title: ", "").strip()
                body = re.sub(r"^(Title:.*\n)?\s*Text \(\d+ chars\):\n", "", text)
                body = re.sub(r"\n?\s*tab_id: \d+\s*$", "", body).strip()
                full = body
                out = full[:max_chars]
                return {"ok": True, "url": url, "title": title, "text": out,
                        "chars": len(out), "full_chars": len(full),
                        "source": "browser:webkit", "errors": [], "links": [],
                        "media": [], "deadline_exceeded": False, "stages_cut": [],
                        "elapsed": round(time.monotonic() - _started, 2)}
        time.sleep(1.5)
    return None


# -------------------------------------------------------------------- the thing
def fetch(url, timeout=25, max_chars=20000, keep_links=False, ttl=86400,
          use_reader=True, use_archive=True, retries=1, deadline=45.0,
          use_vendor=True, use_rewrite=True):
    url = check_url(url)
    hit = cache_get(url, ttl)
    # a cached entry only remembers links if it was collected with keep_links;
    # a caller that needs them (research --follow) must not inherit a linkless hit
    if hit and keep_links and not hit.get("_links_collected"):
        hit = None
    if hit:
        return _shape(hit, max_chars)        # cap applied on the way out, never stored

    hard_end = time.monotonic() + max(1.0, float(deadline))
    deadline_exceeded = False
    stages_cut = []

    # Each stage gets a *share* of the deadline, not the whole thing: a stage
    # that hangs must not starve the stages behind it. Without this the reader
    # relays can eat the entire budget and the vendor stage — the one that
    # actually reaches hostile pages — never runs.
    total = max(1.0, float(deadline))
    before, after = order_rewrites(rewrite_candidates(url) if use_rewrite else [])
    chain = [(f"rewrite:{n}",
              (lambda t, nm=n, tgt=tgt, kd=k: fetch_rewrite(nm, tgt, kd, t, keep_links)), 0.35)
             for n, tgt, k in before]
    chain.append(("direct", lambda t: fetch_direct(url, t, keep_links), 0.35))
    if use_reader:
        chain.append(("reader", lambda t: fetch_reader(url, t, keep_links), 0.40))
    chain += [(f"rewrite:{n}",
               (lambda t, nm=n, tgt=tgt, kd=k: fetch_rewrite(nm, tgt, kd, t, keep_links)), 0.30)
              for n, tgt, k in after]
    if _vendor_enabled(use_vendor):
        chain.append(("vendor", lambda t: fetch_vendor(url, t, keep_links), 0.50))
        chain.append(("exa", lambda t: fetch_exa(url, t, keep_links), 0.45))
    if use_archive:
        chain.append(("archive", lambda t: fetch_archive(url, t, keep_links), 0.30))

    errors = []
    for name, fn, share in chain:
        for attempt in range(retries + 1):
            remaining = hard_end - time.monotonic()
            if remaining <= 0.5:
                deadline_exceeded = True
                errors.append(f"{name}: skipped, deadline ({float(deadline):g}s) reached")
                break
            budget = min(float(timeout), remaining, total * share)
            try:
                res = _run_with_budget(lambda f=fn, t=max(1.0, budget): f(t),
                                       budget, name)
                if not (res.get("text") or "").strip():
                    raise RuntimeError("empty text")
                wall = wall_reason(res["text"])
                if wall:
                    raise RuntimeError(f"wall page, not content ({wall})")
                res["text"] = res["text"][:MAX_INFLATED]   # a storage ceiling, not the caller's cap
                res["ok"] = True
                res["url"] = url
                res["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                res.setdefault("media", [])
                res["errors"] = errors
                # the call succeeded, so the deadline did not cut *it*: record which
                # stages were abandoned instead of pretending the answer is partial
                res["deadline_exceeded"] = False
                res["stages_cut"] = list(stages_cut)
                if keep_links:
                    res["_links_collected"] = True
                cache_put(url, res)          # full text: what the next caller may want
                return _shape(res, max_chars)
            except urllib.error.HTTPError as e:
                errors.append(f"{name}: HTTP {e.code}")
                # a vendor that answered with an error has answered: retrying a
                # borrowed free endpoint is how you get rate-limited
                if e.code in (401, 403, 404, 410, 429) or name == "vendor" or e.code >= 500:
                    break
            except StageTimeout as e:
                deadline_exceeded = True
                stages_cut.append(name)
                errors.append(f"{name}: {e}")
                break
            except Exception as e:
                errors.append(f"{name}: {str(e)[:110]}")
                # borrowed endpoints get one attempt: a refused connection to a
                # free vendor is a no, not an invitation to knock twice
                if name in ("vendor", "exa") or name.startswith("rewrite:"):
                    break
            time.sleep(min(0.8 * (attempt + 1), max(0.0, hard_end - time.monotonic())))

    # --- local patch: browser stage -----------------------------------------
    # On this device r.jina.ai/s.jina.ai answer HTTP 451 (blocked in Iran),
    # Firecrawl and Exa 403, and Wayback does not answer inside the deadline, so
    # the chain above collapses to `direct`. Minis ships a real WebView driven by
    # minis-browser-use; it renders JS and clears most bot walls, which is the only
    # transport left that can read those pages. Opt out with FETCH_BROWSER=0.
    if os.environ.get("FETCH_BROWSER", "1") != "0":
        got = _via_browser(url, max_chars)
        if got:
            got["link_rescue"] = False
            return got
        errors.append("browser: page never painted readable text")

    return {"ok": False, "url": url, "title": "", "text": "", "chars": 0,
            "full_chars": 0,                      # same keys on failure as on success
            "source": None, "errors": errors, "links": [], "media": [],
            "deadline_exceeded": deadline_exceeded, "stages_cut": list(stages_cut)}


# ------------------------------------------------------------------------- cli
_USERINFO = re.compile(r"^(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^/@\s]+)@")


def redact(url):
    """`https://user:pw@host/` must not carry the password into a file we write."""
    m = _USERINFO.match(url or "")
    if not m:
        return url or ""
    user = m.group("user").split(":", 1)[0]
    return f"{m.group('scheme')}{user}:***@" + (url or "")[m.end():]


def yaml_scalar(value):
    """A JSON string is a valid YAML double-quoted scalar, newlines included.

    Page titles are attacker-controlled and routinely contain a colon
    ("Python: The Basics", "Error: Not Found"). Interpolated raw into the
    frontmatter they make the block unparseable — `yaml.safe_load` raised
    "mapping values are not allowed here" on a title from this very suite.
    """
    return json.dumps(str(value), ensure_ascii=False)


def one_line(text, limit=200):
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def link_text(text, limit=120):
    """Anchor text for a markdown link: one line, balanced brackets."""
    return one_line(text, limit).replace("\\", "/").replace("[", "(").replace("]", ")")


def cell_url(url):
    """A URL inside a markdown link: no whitespace, no closing paren, no newline."""
    return re.sub(r"[\s()<>]", lambda m: urllib.parse.quote(m.group(0)), str(url or ""))[:400]


def to_markdown(res):
    fm = [f"url: {yaml_scalar(redact(res['url']))}"]
    for k in ("title", "final_url", "source", "chars", "fetched_at", "snapshot_ts"):
        if res.get(k):
            value = redact(res[k]) if k == "final_url" else res[k]
            fm.append(f"{k}: {yaml_scalar(value)}" if isinstance(value, str)
                      else f"{k}: {value}")
    body = f"---\n" + "\n".join(fm) + "\n---\n\n"
    if res.get("title"):
        heading = re.sub(r"\s+", " ", res["title"]).strip()   # one line, always
        body += f"# {heading}\n\n"
    body += res["text"]
    if res.get("links"):
        # an anchor with a newline or a ] in it breaks the list item and the link
        items = [f"- [{link_text(t)}]({cell_url(u)})" for t, u in res["links"][:80]]
        body += "\n\n## Links\n" + "\n".join(items)
    return body


def _require_dir(path, flag):
    """A destination must be a directory we can write, decided before the work."""
    if not path:
        return
    if os.path.exists(path) and not os.path.isdir(path):
        raise SystemExit(f"{flag} {path}: exists and is not a directory")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        raise SystemExit(f"{flag} {path}: {e.strerror or e}")


def _cli_fetch(url, a):
    """CLI-level wrapper: a URL we refuse becomes a failed result, not a crash."""
    ttl = 0 if a.no_cache else a.ttl
    try:
        return fetch(url, a.timeout, a.max_chars, a.links, ttl,
                     not a.no_reader, not a.no_archive, 1, a.deadline,
                     not a.no_vendor, not a.no_rewrite)
    except ValueError as e:
        return {"ok": False, "url": url, "title": "", "text": "", "chars": 0,
                "full_chars": 0, "source": None, "errors": [str(e)], "links": [],
                "media": [], "deadline_exceeded": False, "stages_cut": []}


def main():
    ap = argparse.ArgumentParser(description="resilient URL -> markdown for agents")
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--out", default=None, help="directory for .md files")
    ap.add_argument("--media", default=None, metavar="DIR",
                    help="download the post's pictures/videos into DIR "
                         "(only reddit and x posts carry media)")
    ap.add_argument("--max-chars", type=pos_int, default=20000)
    ap.add_argument("--timeout", type=pos_int, default=25)
    ap.add_argument("--links", action="store_true")
    ap.add_argument("--ttl", type=nonneg_int, default=86400, help="cache TTL seconds (0 disables)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-reader", action="store_true")
    ap.add_argument("--no-archive", action="store_true")
    ap.add_argument("--no-rewrite", action="store_true",
                    help="skip the machine-readable twins (github raw, StackExchange API, ...)")
    ap.add_argument("--no-vendor", action="store_true",
                    help="skip the vendor extract stages, Firecrawl and Exa (also FETCH_VENDOR=0)")
    ap.add_argument("--deadline", type=pos_float, default=45.0,
                    help="hard wall-clock cap per URL in seconds (default 45); "
                         "a stage that ignores its own timeout is abandoned")
    ap.add_argument("--parallel", type=pos_int, default=4)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--text", action="store_true", help="print the extracted text")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    _require_dir(a.out, "--out")
    _require_dir(a.media, "--media")

    ttl = 0 if a.no_cache else a.ttl
    with ThreadPoolExecutor(max_workers=a.parallel) as ex:
        futs = {u: ex.submit(_cli_fetch, u, a) for u in a.urls}
        # collect in the order the URLs were given: a caller that runs
        # `fetch.py a b c --json` should not have to match results by URL
        results = [futs[u].result() for u in a.urls]

    if a.out:
        os.makedirs(a.out, exist_ok=True)
    for r in results:
        if r.get("ok") and a.out:
            name = re.sub(r"[^a-zA-Z0-9]+", "_", r.get("title") or
                          urllib.parse.urlparse(r["url"]).netloc)[:60].strip("_") or "page"
            p = os.path.join(a.out, name + ".md")
            i = 1
            while os.path.exists(p):
                i += 1
                p = os.path.join(a.out, f"{name}_{i}.md")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(to_markdown(r))
            r["file"] = p
        if r.get("ok") and a.media and r.get("media"):
            files = download_media(r["media"], a.media)
            r["media_files"] = files
            if not a.quiet and not a.json:
                print(f"[media] {len(files)}/{len(r['media'])} saved to {a.media}")

    if a.json:
        # --json is machine output: the text goes *in* the payload, not after it,
        # otherwise the array is followed by "===== url =====" and no parser can read it
        print(json.dumps([{k: v for k, v in r.items() if k != "text" or a.text}
                          for r in results], indent=1))
    elif not a.quiet:
        for r in results:
            if r.get("ok"):
                print(f"[ok]   {r['chars']:>6} chars · {r['source']:<8} · "
                      f"{r['elapsed'] if not r.get('cached') else 0:.1f}s · {r['title'][:70]}")
                if r.get("file"):
                    print(f"       -> {r['file']}")
                if r.get("media"):
                    print(f"       {len(r['media'])} media item(s)")
            else:
                print(f"[fail] {r['url'][:90]}")
                for e in r["errors"][-3:]:
                    print(f"       {e}")
    if a.text and not a.json:
        for r in results:
            if r.get("ok"):
                print(f"\n===== {r['url']} =====\n{r['text']}")

    return 0 if any(r.get("ok") for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
