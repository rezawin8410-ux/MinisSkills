#!/usr/bin/env python3
"""
research.py — deep research in one command: search, read, extract, cite.

Pipeline
  1. expand the question into several queries (depth controls how many)
  2. search every query across several providers in parallel
  3. merge, dedupe, rank by relevance + cross-provider agreement + domain diversity
  4. fetch the top N pages with the resilient fetcher (direct -> reader -> archive)
  4b. with --follow: read the outbound links of those pages, pick the ones that
      look like they answer the question, fetch them too and mark them as hop 2
  5. extract the sentences that actually answer the question (tf-idf style),
     plus every number, date and unit the sources contain
  6. write a cited markdown report + a JSON sidecar with the full trail

  research.py "does vheer allow commercial use of free image-to-image"
  research.py "free tts api" -o tts.md --depth 3 --max-sources 10
  research.py "ffmpeg zoompan" --group tech --json --no-fetch
  research.py "free tts api" --follow 2                  # + follow links found on the pages
  research.py "piper tts" --follow 3 --follow-budget 6   # cap the second hop

Exit codes: 0 ok · 2 nothing could be read
"""
import argparse
import json
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch as F
import search as S

try:
    from _cachekit import nonneg_int as _nonneg_int
    from _cachekit import pos_int as _pos_int
except ImportError:                     # a lone copy: same behaviour, same messages
    import argparse as _argparse

    def _pos_int(text):
        n = int(text)
        if n < 1:
            raise _argparse.ArgumentTypeError(f"must be 1 or more (got {n})")
        return n

    def _nonneg_int(text):
        n = int(text)
        if n < 0:
            raise _argparse.ArgumentTypeError(f"must be 0 or more (got {n})")
        return n

STOP = set("""a an the and or but if then than that this these those of in on at to for from
with without into over under about as by is are was were be been being do does did doing
have has had having it its it's we our you your they their he she them i not no nor so such
can could should would may might must will just also more most other some any each every
what which who whom whose when where why how all both few many much s t""".split())


# ------------------------------------------------------------------ expansion
def expand(q, depth):
    q = q.strip()
    base = [q]
    if depth <= 1:
        return base
    variants = [q + " 2026", q + " explained"]
    if re.search(r"\b(how|guide|tutorial|setup)\b", q, re.I):
        variants.append(q + " example")
    if re.search(r"\b(vs|versus|compare|best)\b", q, re.I):
        variants.append(q + " comparison")
    if re.search(r"\b(rate limit|quota|limit|error|fix|fails?)\b", q, re.I):
        variants.append(q + " github issue")
    out = base + variants[:2 if depth == 2 else 4]
    return list(dict.fromkeys(out))


# ------------------------------------------------------------------- scoring
def terms_of(text):
    return [w for w in re.findall(r"[a-z0-9][a-z0-9\-.]{2,}", text.lower()) if w not in STOP]


def split_sentences(text):
    text = re.sub(r"\s+", " ", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", text)
    return [p.strip() for p in parts if 40 <= len(p) <= 400]


def summarise(docs, question, per_doc=5):
    """Extractive tf-idf over all fetched docs, then pick the best sentences per doc."""
    qterms = set(terms_of(question))
    df = {}
    for d in docs:
        for w in set(terms_of(d["text"])):
            df[w] = df.get(w, 0) + 1
    n = max(1, len(docs))
    idf = {w: math.log(1 + n / (1 + c)) for w, c in df.items()}

    out = {}
    for d in docs:
        sents = split_sentences(d["text"])
        if not sents:
            continue
        scored = []
        for i, s in enumerate(sents):
            ts = terms_of(s)
            if not ts:
                continue
            score = sum(idf.get(w, 1.0) for w in set(ts) if w in qterms) * 2.5
            score += sum(idf.get(w, 0.0) for w in set(ts)) * 0.06
            score += 1.2 if i < 4 else 0                       # lead paragraphs matter
            score += 0.6 if re.search(r"\d", s) else 0          # concrete > vague
            score -= 0.4 if s.startswith(("It ", "This ", "They ")) else 0
            scored.append((score, i, s))
        scored.sort(reverse=True)
        picked, seen_txt = [], []
        for score, i, s in scored:
            sig = set(terms_of(s))
            if any(len(sig & p) / max(1, len(sig | p)) > 0.7 for p in seen_txt):
                continue
            seen_txt.append(sig)
            picked.append((i, s))
            if len(picked) >= per_doc:
                break
        out[d["url"]] = [s for _, s in sorted(picked)]
    return out


NUM = re.compile(r"(?<![\w.])(?:US)?\$?\d[\d,.]*\s?(?:%|MB|GB|KB|TB|px|s\b|ms|min|h\b|hours|"
                 r"tokens|credits|req(?:uests)?|rps|LUFS|fps|kbps|days?|months?|years?|clips?|"
                 r"voices?|chars?|languages?)?", re.I)


def numbers(docs):
    found = {}
    for d in docs:
        for m in NUM.finditer(d["text"]):
            v = m.group(0).strip().rstrip(".")
            if len(v) < 2 or not re.search(r"\d", v):
                continue
            if re.fullmatch(r"\d{1,2}", v):        # bare small integers are noise
                continue
            found.setdefault(v, set()).add(d["url"])
    ranked = sorted(found.items(), key=lambda kv: -len(kv[1]))[:18]
    return [(v, len(urls)) for v, urls in ranked]


# ---------------------------------------------------------------------- main
def run(question, depth=2, max_sources=8, group="web", providers=None,
        do_fetch=True, per_doc=5, max_chars=18000, follow=0, follow_budget=6):
    queries = expand(question, depth)
    hits, errors = [], {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(S.search, q, providers, group, max_sources * 2): q for q in queries}
        for f in as_completed(futs):
            try:
                r = f.result()
                hits.extend(r["results"])
                errors.update(r.get("errors") or {})
            except Exception as e:
                errors[futs[f]] = str(e)[:120]

    merged = S.trim(S.rank(S.dedupe(hits), question), max_sources * 2, per_domain=2)
    # the report describes its own method, so the method has to be recorded, not
    # remembered: this used to be a hardcoded "(Bing, Bing News, Wikipedia)" line
    # that was wrong for --group tech/academic/all and for every --provider run
    used = list(providers or S.GROUPS.get(group, S.GROUPS["web"]))
    if not do_fetch:
        return {"question": question, "queries": queries, "sources": [], "candidates": merged,
                "errors": errors, "summaries": {}, "numbers": [],
                "group": group, "providers": used}

    docs, failures = [], []
    want_links = follow > 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(F.fetch, r["url"], 25, max_chars, want_links): r for r in merged}
        for f in as_completed(futs):
            cand = futs[f]
            try:
                d = f.result()
            except Exception as e:
                failures.append({"url": cand["url"], "errors": [str(e)[:120]]})
                continue
            if d.get("ok"):
                d["search_title"] = cand["title"]
                d["providers"] = cand.get("providers", [])
                docs.append(d)
            else:
                failures.append({"url": cand["url"], "errors": d.get("errors", [])[:3]})

    docs = [d for d in docs if relevant(d, question)]
    docs = sorted(docs, key=lambda d: -min(len(d.get("text", "")), 12000))[:max_sources]
    docs.sort(key=lambda d: next((r["rank"] for r in merged if r["url"] == d["url"]), 99))
    for d in docs:
        d["hop"] = 1

    followed, link_map = [], {}
    if want_links and docs:
        followed, follow_failures, link_map = follow_links(
            docs, question, follow, follow_budget, max_chars)
        failures.extend(follow_failures)
        docs = docs + followed

    sums = summarise(docs, question, per_doc)
    return {"question": question, "queries": queries, "sources": docs,
            "failures": failures, "errors": errors, "summaries": sums,
            "numbers": numbers(docs), "candidates": merged,
            "group": group, "providers": used,
            "followed": [{"url": d["url"], "from": d.get("via_link_from"),
                          "anchor": d.get("anchor"), "chars": d.get("chars")}
                         for d in followed],
            "links_considered": {k: v for k, v in link_map.items() if v}}


def relevant(doc, question, need=2):
    """Cheap but effective relevance gate: off-topic pages must not reach the report."""
    terms = [t for t in set(terms_of(question)) if len(t) > 3]
    if not terms:
        return True
    probe = ((doc.get("title") or "") + " " + (doc.get("search_title") or "") + " " +
             (doc.get("text") or "")[:4000]).lower()
    hits = sum(1 for t in terms if t in probe)
    in_title = any(t in ((doc.get("title") or "") + (doc.get("search_title") or "")).lower()
                   for t in terms)
    return hits >= need and (in_title or hits >= need + 1)


# --------------------------------------------------------------------- follow
JUNK_LINK = re.compile(r"(/(login|signin|signup|register|cart|checkout|account|settings)\b|"
                       r"/(tag|tags|category|categories|author|page)/|\?replytocom=|"
                       r"\.(zip|tar|gz|exe|dmg|iso|png|jpe?g|gif|svg|mp4|mp3|woff2?)($|\?)|"
                       r"(mailto:|javascript:|tel:)|/(privacy|terms|cookies)\b|#)", re.I)
SOCIAL = re.compile(r"(facebook|twitter|x\.com|linkedin|reddit|youtube|instagram|pinterest|"
                    r"t\.me|whatsapp|news\.ycombinator\.com/item)", re.I)


def pick_links(doc, question, n=2, seen=None, same_domain_ok=True):
    """Choose outbound links from a fetched page that are worth a second hop.

    Scoring is deliberately cheap and explainable: anchor text and URL overlap
    with the question's terms, minus nav chrome. One hop only — a crawler is a
    different tool with a different failure mode.
    """
    seen = seen or set()
    terms = [t for t in set(terms_of(question)) if len(t) > 3]
    if not terms:
        return []
    host = re.sub(r"^https?://(www\.)?", "", doc["url"]).split("/")[0].lower()
    out = []
    for anchor, url in doc.get("links") or []:
        if not url.startswith("http") or JUNK_LINK.search(url) or SOCIAL.search(url):
            continue
        if url in seen:
            continue
        ahost = re.sub(r"^https?://(www\.)?", "", url).split("/")[0].lower()
        if ahost == host and not same_domain_ok:
            continue
        if len(anchor.strip()) < 8:
            continue                       # "here", "read more", "docs"
        blob = (anchor + " " + url).lower()
        hits = sum(1 for t in terms if t in blob)
        if hits == 0:
            continue
        score = hits * 2 + min(len(anchor) / 40.0, 1.0)
        if ahost == host:
            score -= 1.0                   # a site linking to itself is worth less
        out.append((score, anchor[:80], url))
    out.sort(reverse=True)
    picked, urls, hosts = [], set(), {}
    for score, anchor, url in out:
        if url in urls:
            continue
        ahost = re.sub(r"^https?://(www\.)?", "", url).split("/")[0].lower()
        if hosts.get(ahost, 0) >= 1 and len(out) > n:
            continue                      # one hop per host: don't chase a whole blog
        urls.add(url)
        hosts[ahost] = hosts.get(ahost, 0) + 1
        picked.append({"url": url, "anchor": anchor, "score": round(score, 2),
                       "from": doc["url"]})
        if len(picked) >= n:
            break
    return picked


def follow_links(docs, question, per_page=2, budget=6, max_chars=18000):
    """Hop 2: fetch the best outbound links of the pages we already read."""
    seen = {d["url"] for d in docs}
    picks, per_doc = [], {}
    for d in docs:
        got = pick_links(d, question, per_page, seen)
        per_doc[d["url"]] = got
        for p in got:
            if p["url"] not in seen:
                seen.add(p["url"])
                picks.append(p)
    picks = picks[:budget]

    extra, failures = [], []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(F.fetch, p["url"], 20, max_chars, True): p for p in picks}
        for f in as_completed(futs):
            p = futs[f]
            try:
                d = f.result()
            except Exception as e:
                failures.append({"url": p["url"], "errors": [str(e)[:120]], "hop": 2})
                continue
            if not d.get("ok"):
                failures.append({"url": p["url"], "errors": d.get("errors", [])[:2], "hop": 2})
                continue
            d["hop"] = 2
            d["via_link_from"] = p["from"]
            d["anchor"] = p["anchor"]
            d["search_title"] = d.get("title") or p["anchor"]
            extra.append(d)
    extra = [d for d in extra if relevant(d, question)]
    return extra, failures, per_doc


def cell(value, limit=70):
    """One line, one cell. A title with a newline used to split the row in two and
    a `|` in a title silently added a column."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.replace("|", "/").replace("\\", "/")[:limit]


def to_markdown(res):
    q = res["question"]
    docs = res["sources"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = [f"---", f"question: {json.dumps(q)}", f"generated: {now}",
         f"sources: {len(docs)}", f"queries: {len(res['queries'])}", "---", "",
         f"# Research: {q}", "",
         f"*{len(docs)} sources read · {now} · queries: "
         + " · ".join(f"`{x}`" for x in res["queries"]) + "*", ""]

    # ---- TL;DR: best sentences across sources, one per source first
    L.append("## What the sources actually say\n")
    for i, d in enumerate(docs, 1):
        sents = res["summaries"].get(d["url"]) or []
        if not sents:
            continue
        dom = re.sub(r"^https?://(www\.)?", "", d["url"]).split("/")[0]
        L.append(f"**[{i}] {cell(d.get('title') or d.get('search_title') or dom)}** — {dom}")
        for s in sents[:3]:
            L.append(f"- {s}  \n  <sub>[{i}]</sub>")
        L.append("")

    if res.get("numbers"):
        L.append("## Numbers, units and dates worth checking\n")
        L.append("| value | seen in |")
        L.append("|---|---|")
        for v, c in res["numbers"][:14]:
            L.append(f"| `{cell(v, 40)}` | {c} source(s) |")
        L.append("")

    L.append("## Sources\n")
    L.append("| # | title | domain | chars | hop | via | url |")
    L.append("|---|---|---|---|---|---|---|")
    for i, d in enumerate(docs, 1):
        dom = cell(re.sub(r"^https?://(www\.)?", "", d["url"]).split("/")[0], 40)
        title = cell(d.get("title") or d.get("search_title") or "")
        hop = d.get("hop", 1)
        via = d.get("via_link_from")
        if via:
            # hoisted out of the f-string: Python < 3.12 forbids backslashes
            # inside an f-string expression part (PEP 701 lifted that)
            vhost = cell(re.sub(r"^https?://(www\.)?", "", via).split("/")[0], 40)
            vtxt = cell(f"link from {vhost}", 40)
        else:
            vtxt = cell(d.get("source"), 40)
        L.append(f"| [{i}] | {title} | {dom} | {d.get('chars', 0)} | {hop} | "
                 f"{vtxt} | {cell(d['url'], 200)} |")
    L.append("")

    if res.get("followed"):
        L.append(f"## Followed links (hop 2, {len(res['followed'])} page(s))\n")
        L.append("Read because they were linked from a page that answered the question — "
                 "a search engine never ranked them.\n")
        for f in res.get("followed", [])[:10]:
            src = re.sub(r"^https?://(www\.)?", "", f.get("from") or "").split("/")[0]
            L.append(f"- *{cell(f.get('anchor') or '(no anchor)', 90)}* — "
                     f"{cell(f['url'], 160)}  \n"
                     f"  <sub>linked from {src}, {f.get('chars', 0)} chars</sub>")
        L.append("")

    if res.get("failures"):
        L.append("## Could not read (paywall, 403, JS-only)\n")
        for f in res["failures"][:10]:
            L.append(f"- {cell(f['url'], 120)} — {cell('; '.join(f['errors'][:2]), 120)}")
        L.append("")

    if res.get("errors"):
        L.append("## Provider warnings\n")
        for k, v in list(res["errors"].items())[:8]:
            L.append(f"- **{k}**: {v[:140]}")
        L.append("")

    L.append("## Method\n")
    grp = res.get("group") or "web"
    prov = res.get("providers") or S.GROUPS.get(grp) or S.GROUPS["web"]
    L.append("1. Question expanded into "
             f"{len(res['queries'])} queries; each searched across the `{grp}` provider group "
             f"({', '.join(prov)}) in parallel.")
    L.append("2. Results merged, deduped by URL, ranked by relevance + cross-provider "
             "agreement + domain diversity (max 2 per domain).")
    L.append("3. Top pages fetched through the staged chain (direct → alternate front-ends "
             "→ reader proxies → vendor → Wayback, each under a shared deadline); HTML "
             "reduced to main content, boilerplate dropped.")
    L.append("4. Sentences scored by idf-weighted overlap with the question, "
             "de-duplicated at 70% similarity; numbers extracted by regex.")
    if res.get("followed"):
        L.append(f"5. **Hop 2:** {len(res['followed'])} outbound link(s) from the pages above "
                 "were followed one hop, re-checked for relevance and added as sources.")
    L.append("")
    L.append(f"*Full trail (every candidate, provider, error and raw extract): "
             f"`{os.path.basename(res.get('_json_path', 'report.json'))}`*")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="deep research: search -> read -> cite")
    ap.add_argument("question", nargs="+")
    ap.add_argument("-o", "--out", default=None, help="report.md path")
    ap.add_argument("--depth", type=int, default=2, choices=[1, 2, 3])
    ap.add_argument("--max-sources", type=_pos_int, default=8)
    ap.add_argument("--group", default="web", choices=["web", "tech", "academic", "all"])
    ap.add_argument("--provider", action="append", default=None)
    ap.add_argument("--per-doc", type=_nonneg_int, default=5)
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--follow", type=_nonneg_int, default=0, metavar="N",
                    help="follow up to N outbound links per source page (hop 2)")
    ap.add_argument("--follow-budget", type=_pos_int, default=6, metavar="M",
                    help="max total hop-2 pages (default 6)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    question = " ".join(a.question).strip()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = a.out or f"research_{stamp}.md"

    res = run(question, a.depth, a.max_sources, a.group, a.provider,
              not a.no_fetch, a.per_doc, follow=a.follow, follow_budget=a.follow_budget)
    res["_json_path"] = os.path.splitext(out)[0] + ".json"

    # -o reports/run.md should create reports/, not die with a traceback after
    # the whole search and fetch has already been paid for. An unusable path is
    # caught here, before the run, for the same reason.
    if os.path.isdir(out):
        raise SystemExit(f"-o {out}: is a directory, give it a file name")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    except OSError as e:
        raise SystemExit(f"-o {out}: {e.strerror or e}")

    try:
        with open(os.path.splitext(out)[0] + ".json", "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in res.items() if k != "summaries"} | {
                "summaries": res.get("summaries", {})}, f, indent=1, default=str)
    except OSError as e:
        # -o /dev/null, a read-only mount, a full disk: lose the trail, not the work
        print(f"warning: could not write the trail next to {out}: {e}", file=sys.stderr)
        res["_json_path"] = "(not written)"

    if a.no_fetch:
        print(json.dumps({"question": question, "queries": res["queries"],
                          "candidates": res["candidates"], "errors": res["errors"]}, indent=1)
                         if a.json else "\n".join(
                             f"{r['rank']:>2}. {r['title'][:80]}\n    {r['url']}"
                             for r in res["candidates"]))
        return 0

    md = to_markdown(res)
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)

    if a.json:
        print(json.dumps({"ok": bool(res["sources"]), "report": out,
                          "json": res["_json_path"], "sources": len(res["sources"]),
                          "queries": res["queries"],
                          "failed": len(res.get("failures", []))}))
    else:
        n2 = len(res.get("followed") or [])
        print(f"report: {out}  ({len(res['sources'])} sources"
              + (f", {n2} followed" if n2 else "")
              + f", {len(res.get('failures', []))} unreadable)")
        print(f"trail : {res['_json_path']}")
    return 0 if res["sources"] else 2


if __name__ == "__main__":
    sys.exit(main())
