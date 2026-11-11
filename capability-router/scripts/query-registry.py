#!/usr/bin/env python3
"""Query the aswath-capability registry.

This is the resolution layer: given a capability or a free-text need, it returns
the best skills to invoke, ranked by quality, then cost, then popularity.

Ranking order
  1. quality score from the ledger (your accept/reject history) - descending
  2. in-stock local over marketplace-only (marketplace needs an install)
  3. popularity (marketplace installs) - descending
  4. token cost on invoke - ascending (cheap skills win ties)

Usage:
    python query-registry.py cap design-critique
    python query-registry.py need "roast my landing page and fix it"
    python query-registry.py list --tag design-system
    python query-registry.py install "database migration"
    python query-registry.py show impeccable
    python query-registry.py stats
"""

import argparse
import json
import re
import os
import sys
from pathlib import Path

STOP = set("""a an the my our your this that these those i me we it is are was were be been being
to of for on in with and or but if then than so as at by from into about over under need needs
want make build create design page ui work thing stuff help please""".split())


# --- shared router state -------------------------------------------------
# The router is installed in more than one skills root (mcode reads its own
# root, other harnesses read theirs). The mutable state - the registry index
# and the outcome ledger - must have exactly one copy, or the two installs
# drift and the router ranks differently depending on who is asking.
# Shared location: ~/.minimax/local-runtime/skill-learning/
def shared_state_dir():
    """Where the mutable router state lives.

    Overridable so the router can run from a checkout, a container or a
    CI job instead of one machine's home directory.
    """
    env = os.environ.get("SKILL_ROUTER_STATE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".minimax" / "local-runtime" / "skill-learning"


def shared_registry_path():
    return shared_state_dir() / "skills.json"


def shared_ledger_path():
    return shared_state_dir() / "ledger.json"


def registry_path(home=None):
    p = shared_registry_path()
    if p.is_file():
        return p
    raise SystemExit("registry not found - run scripts/build-registry.py first")


def load(home=None):
    p = registry_path(home)
    return json.loads(p.read_text(encoding="utf-8"))


def slugify(name):
    import re
    return re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").lower()).strip("-")


def resolve(reg, key):
    """Find a skill by its name OR its kebab slug.

    Plugin skills declare names like 'Agent Cost Report' but are referenced
    everywhere as 'agent-cost-report'. Both must resolve.
    """
    want = (key or "").strip().lower()
    want_slug = slugify(want)
    for s in all_skills(reg):
        if (s.get("name") or "").lower() == want:
            return s
    for s in all_skills(reg):
        if s.get("slug") == want_slug:
            return s
    for s in all_skills(reg):
        if slugify(s.get("name")) == want_slug:
            return s
    return None


def load_capability_map(reg):
    """Capability taxonomy, resolved relative to the registry file."""
    p = shared_state_dir() / "capabilities.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("capabilities", {})
    except (OSError, ValueError):
        return {}


def all_skills(reg):
    return (list(reg.get("local", []))
            + list(reg.get("plugin", []))
            + list(reg.get("marketplace", [])))


def tokens_of(s):
    return s.get("tokens_est") or 0


def quality_of(s):
    q = s.get("quality")
    if isinstance(q, dict):
        return q.get("score", 0)
    return q or 0


def rank(skills):
    def key(s):
        return (
            -quality_of(s),
            0 if s.get("source") in ("local", "plugin") else 1,
            -(s.get("installs") or 0),
            tokens_of(s),
            s.get("name") or "",
        )
    return sorted(skills, key=key)


def match_caps(reg, cap):
    out = []
    for s in all_skills(reg):
        if cap in (s.get("tags") or []):
            out.append(s)
    return out


def free_text(text):
    words = [w for w in re.findall(r"[a-z0-9\-]+", text.lower()) if w not in STOP and len(w) > 2]
    return words


def infer_capabilities(text, capability_map):
    """Which capabilities does this request talk about?

    For a user request both strong and weak keywords count - the user naming
    'cart' is a direct statement of need, unlike a skill description that
    merely mentions the word.
    """
    low = (text or "").lower()
    hits = []
    for cap, spec in capability_map.items():
        if isinstance(spec, dict):
            keywords = list(spec.get("strong") or []) + list(spec.get("weak") or [])
        else:
            keywords = spec or []
        for kw in keywords:
            if kw.lower() in low:
                hits.append(cap)
                break
    return hits


def match_text(reg, text, capability_map=None):
    """Rank skills against a free-text need.

    Primary signal is capability overlap, because a tag means the skill's own
    description claimed that job. Free-text word hits are a weaker signal and
    are kept only as a tiebreak, so a stray word in a description cannot
    outrank a genuine capability match.
    """
    capability_map = capability_map or load_capability_map(reg)
    caps = set(infer_capabilities(text, capability_map))
    words = free_text(text)
    out = []
    for s in all_skills(reg):
        stags = set(s.get("tags") or [])
        cap_overlap = len(caps & stags)
        name = (s.get("name") or "").lower() + " " + (s.get("slug") or "")
        desc = (s.get("description") or "").lower()
        name_hits = sum(1 for w in words if w in name)
        desc_hits = sum(1 for w in words if w in desc)
        score = cap_overlap * 10 + name_hits * 3 + desc_hits
        if score <= 0:
            continue
        s = dict(s)
        s["_match"] = score
        s["_caps"] = sorted(caps & stags)
        out.append(s)
    out.sort(key=lambda s: (-s["_match"], -quality_of(s), 0 if s.get("source") in ("local", "plugin") else 1, tokens_of(s)))
    return out


def fmt(s, verbose=False):
    bits = [f"  {s.get('name','?'):<40}"]
    bits.append(f"{'local' if s.get('source')=='local' else 'mkt':<5}")
    bits.append(f"{tokens_of(s):>6}tok")
    if s.get("installs"):
        bits.append(f"inst={s['installs']}")
    q = quality_of(s)
    if q:
        bits.append(f"q={q}")
    if s.get("tags"):
        bits.append(f"  [{','.join(s['tags'][:4])}]")
    if s.get("_caps"):
        bits.append(f"  <-{','.join(s['_caps'])}")
    if verbose and s.get("description"):
        bits.append("\n      " + s["description"][:220].replace("\n", " "))
    if verbose and s.get("plugin"):
        bits.append(f"\n      plugin: {s['plugin']}")
    return " ".join(bits)


def cmd_cap(reg, args):
    caps = match_caps(reg, args.name)
    if not caps:
        print(f"no skills tagged '{args.name}'")
        return 1
    print(f"== capability: {args.name} ({len(caps)} skills) ==")
    for s in rank(caps)[:args.limit]:
        print(fmt(s, args.verbose))
    return 0


def cmd_need(reg, args):
    cap_map = load_capability_map(reg)
    hits = match_text(reg, args.text, cap_map)
    if not hits:
        print("no match")
        return 1
    print(f"== need: {args.text} ({len(hits)} candidates) ==")
    print(f"   inferred capabilities: {', '.join(infer_capabilities(args.text, cap_map)) or 'none'}")
    for s in hits[:args.limit]:
        print(fmt(s, args.verbose))
    return 0


def cmd_list(reg, args):
    hits = all_skills(reg)
    if args.tag:
        hits = [s for s in hits if args.tag in (s.get("tags") or [])]
    if args.source:
        hits = [s for s in hits if s.get("source") == args.source]
    print(f"== {len(hits)} skills ==")
    for s in rank(hits)[:args.limit]:
        print(fmt(s, args.verbose))
    return 0


def cmd_install(reg, args):
    """Marketplace plugins that could fill a gap, with what they ship."""
    words = free_text(args.text)
    scored = []
    for p in reg.get("installable", []):
        hay = f"{p.get('plugin','')} {p.get('summary','')}".lower()
        score = sum(1 for w in words if w in hay)
        if score:
            p = dict(p)
            p["_match"] = score
            scored.append(p)
    scored.sort(key=lambda p: -p["_match"])
    if not scored:
        print("no installable plugin matches")
        return 1
    print(f"== installable candidates for: {args.text} ({len(scored)}) ==")
    for p in scored[:args.limit]:
        print(f"  {p.get('plugin','?'):<44} v{p.get('version','?')}")
        print(f"      {p.get('summary','')[:200]}")
        print(f"      repo: {p.get('repo')}{('  path:'+p['repo_path']) if p.get('repo_path') else ''}")
        print(f"      reviewed_by: {p.get('reviewed_by')}")
    return 0


def cmd_show(reg, args):
    s = resolve(reg, args.name)
    if s:
        print(json.dumps(s, indent=2, ensure_ascii=False))
        return 0
    print(f"not found: {args.name}")
    return 1


def cmd_stats(reg, args):
    c = reg.get("counts", {})
    print(f"generated_at : {reg.get('generated_at')}")
    for k, v in c.items():
        print(f"{k:<12}: {v}")
    local = reg.get("local", [])
    cap_counts = {}
    for s in local:
        for t in s.get("tags") or []:
            cap_counts[t] = cap_counts.get(t, 0) + 1
    print("\nlocal coverage by capability:")
    for t, n in sorted(cap_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {t:<24} {n}")
    print("\ntop local skills by cost (cheapest first):")
    for s in sorted(local, key=lambda x: tokens_of(x))[:15]:
        print(f"  {s['name']:<38} {tokens_of(s):>6}tok")
    print("\nmost expensive local skills:")
    for s in sorted(local, key=lambda x: -tokens_of(x))[:10]:
        print(f"  {s['name']:<38} {tokens_of(s):>6}tok")
    return 0


def cmd_gap(reg, args):
    """What does this need, and what do we not have for it?

    A gap is a capability the request depends on where no local skill covers
    it. Gaps are the signal to author a skill or stage a marketplace install,
    so they are reported loudly rather than silently answered with noise.
    """
    cap_map = load_capability_map(reg)
    caps = infer_capabilities(args.text, cap_map)
    if not caps:
        print("no capabilities inferred from that text - try naming the job explicitly")
        return 1
    local = reg.get("local", []) + reg.get("plugin", [])
    print(f"== gap analysis: {args.text} ==")
    print(f"   inferred capabilities: {', '.join(caps)}\n")
    gaps = []
    for cap in caps:
        have = [s for s in local if cap in (s.get("tags") or [])]
        if have:
            have.sort(key=lambda s: (tokens_of(s), s.get("name", "")))
            names = ", ".join(f"{s['name']}({tokens_of(s)}tok)" for s in have[:5])
            print(f"  [ok]   {cap:<22} {len(have)} local: {names}")
        else:
            gaps.append(cap)
            print(f"  [GAP]  {cap:<22} no local skill covers this")
    if gaps:
        print(f"\n   {len(gaps)} gap(s): {', '.join(gaps)}")
        # Search the marketplace using each gap's own strong vocabulary, not the
        # request's generic words - "site" should not surface a gallery plugin.
        needles = set()
        for g in gaps:
            spec = cap_map.get(g) or {}
            kws = list(spec.get("strong") or []) if isinstance(spec, dict) else list(spec or [])
            needles.update(k.lower() for k in kws)
        needles = {n for n in needles if len(n) > 3}
        print("   -> author a skill, or stage a marketplace install:")
        scored = []
        for p in reg.get("installable", []):
            hay = f"{p.get('plugin','')} {p.get('summary','')}".lower()
            hits = [n for n in needles if n in hay]
            if hits:
                scored.append((len(hits), len(hay), p, hits))
        scored.sort(key=lambda t: (-t[0], t[1]))
        seen = set()
        shown = 0
        for _, _, p, hits in scored:
            if p.get("plugin") in seen:
                continue
            seen.add(p.get("plugin"))
            print(f"      {p.get('plugin')}  v{p.get('version')}  "
                  f"[{','.join(sorted(hits)[:3])}]")
            print(f"        {(p.get('summary') or '')[:110]}")
            shown += 1
            if shown >= args.limit:
                break
        if not shown:
            print("      (no marketplace plugin matches these gaps - author the skill)")
    else:
        print("\n   no gaps - every inferred capability has local coverage")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=None)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--limit", type=int, default=20)
    common.add_argument("--verbose", "-v", action="store_true")

    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("cap", parents=[common]); p.add_argument("name")
    p = sub.add_parser("need", parents=[common]); p.add_argument("text")
    p = sub.add_parser("list", parents=[common]); p.add_argument("--tag"); p.add_argument("--source", choices=["local", "plugin", "marketplace"])
    p = sub.add_parser("install", parents=[common]); p.add_argument("text")
    p = sub.add_parser("show", parents=[common]); p.add_argument("name")
    p = sub.add_parser("gap", parents=[common]); p.add_argument("text")
    sub.add_parser("stats", parents=[common])

    args = ap.parse_args()
    reg = load(args.home)
    fn = {"cap": cmd_cap, "need": cmd_need, "list": cmd_list, "install": cmd_install,
          "show": cmd_show, "stats": cmd_stats, "gap": cmd_gap}[args.cmd]
    return fn(reg, args)


if __name__ == "__main__":
    sys.exit(main() or 0)
