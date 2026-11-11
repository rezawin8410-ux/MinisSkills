#!/usr/bin/env python3
"""Invoke the specific skill a need actually calls for.

The router used to resolve and report. This makes it resolve and *act*.

    python scripts/invoke.py "<need>"              # decide + show the exact call
    python scripts/invoke.py "<need>" --load       # decide + print the skill body
    python scripts/invoke.py "<need>" --json       # machine readable
    python scripts/invoke.py --skill <name> --load # load a specific skill by name

Resolution rules, in order:
  1. capability overlap with the request (a tag means the skill claimed that job)
  2. quality score from the ledger (your accept/reject history)
  3. local/plugin over marketplace-only (marketplace needs an install first)
  4. token cost, cheapest first

A skill that costs 9.8k tokens to do a 190-token job is a failure. The cost gate
is applied before the final pick, not after it.
"""

import argparse
import json
import re
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent

STOP = set("""a an the my our your this that these those i me we it is is are was were be been being
to of for on in with and or but if then than so as at by from into about over under need needs
want make build create design page ui work thing stuff help please
new old one two three all any some more most can could will would shall should must may
let its like also just only other others own same such very own them they he she his her
here there now next last good better best way ways time times day days year years
what which who whom whose where when why how yet still even ever never always
thing things kind sort part side end top bottom left right first second third
""".split())


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


def skill_root(home=None):
    """Locate aswath-capability: the copy beside this script wins."""
    cand = SKILL_ROOT
    if (cand / "SKILL.md").is_file():
        return cand
    base = Path(home) if home else Path.home()
    for b in (base / ".minimax" / "skills", base / ".agents" / "skills", base / ".claude" / "skills"):
        p = b / "aswath-capability"
        if (p / "SKILL.md").is_file():
            return p
    raise SystemExit("aswath-capability skill not found")


def registry_path(root):
    return shared_registry_path()


def load_registry(root):
    p = registry_path(root)
    if not p.is_file():
        raise SystemExit("registry not found - run scripts/build-registry.py first")
    return json.loads(p.read_text(encoding="utf-8"))


def load_capabilities(root):
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


def slugify(name):
    return re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").lower()).strip("-")


def resolve(reg, key):
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


def _stem(word):
    """Fold a word to a comparable form: tests -> test, boxes -> box."""
    w = (word or "").lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("es") and not w.endswith("ss"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


INFLECTIONS = ("ing", "ed", "ers", "er", "es", "s", "ion", "ions",
               "ment", "ments", "ation", "ations")

# Compound names are written without a separator: aswath-csvjson is a
# CSV-to-JSON tool. A name token is split only when the WHOLE token
# decomposes into known technical terms, so 'csvjson' yields csv and
# json while 'automation' and 'clickmeeting' stay whole.
TECH_ABBREV = ("csv", "json", "html", "yaml", "cli", "sdk", "http", "llm",
               "graphql", "docker", "postgres", "typescript", "javascript",
               "markdown", "tailwind", "prisma", "trpc")


def _compound_parts(token):
    """Split a token into known technical terms, or nothing."""
    if len(token) < 6 or not token.isalnum():
        return []
    parts = []
    rest = token
    guard = 0
    while rest and guard < 6:
        guard += 1
        hit = None
        for ab in TECH_ABBREV:
            if rest.startswith(ab):
                hit = ab
                break
        if not hit:
            return []
        parts.append(hit)
        rest = rest[len(hit):]
    return parts if rest == "" and len(parts) > 1 else []


def _words_match(a, b):
    """Do a request word and a skill word mean the same thing?

    Exact match first. Then a prefix match, but only when the longer word
    carries an inflection suffix, because English morphology does not
    collapse cleanly: brainstorm/brainstorming, plan/planning,
    write/writing and test/testing are all the same word to a reader.
    A bare prefix match would read 'site' as matching 'sitespeakai'.
    """
    if a == b:
        return True
    if len(a) < 4 or len(b) < 4:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if not long.startswith(short):
        return False
    return long.endswith(INFLECTIONS)


def free_text(text):
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [w for w in (_stem(x) for x in words)
            if w not in STOP and len(w) > 2]


def short_text(text):
    """Short words too, for matching against names.

    'md' is two letters and it is exactly what tells claude-md-improver
    apart from claude-certification. Descriptions are too noisy for
    two-letter words, names are not.
    """
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [w for w in (_stem(x) for x in words)
            if w not in STOP and len(w) > 1]


def infer_capabilities(text, cap_map):
    """Which capabilities does this request talk about, and how firmly?

    Strong keywords are phrases and match as written. Weak keywords are
    matched per token, so 'token' still catches 'tokens' but a request
    about 'latest' is not read as a request about testing.

    The weight matters. 'write unit tests for the auth module' mentions
    the auth capability only through the weak keyword 'auth', and the
    testing capability through the strong phrase 'unit test'. A request
    that names a capability in passing must not outrank a request that
    names it in full.
    """
    low = (text or "").lower()
    words = set(re.findall(r"[a-z0-9]+", low))
    weights = {}
    for cap, spec in cap_map.items():
        if isinstance(spec, dict):
            strong = list(spec.get("strong") or [])
            weak = list(spec.get("weak") or [])
        else:
            strong, weak = list(spec or []), []
        weight = 0
        pos = 10 ** 6
        for kw in strong:
            k = kw.lower()
            if k in low:
                weight = 2
                pos = min(pos, low.find(k))
                break
        if not weight:
            for kw in weak:
                k = kw.lower().strip()
                if not k:
                    continue
                hit = False
                if " " in k or "-" in k:
                    if k in low:
                        hit = True
                        pos = min(pos, low.find(k))
                elif len(k) >= 3 and any(w.startswith(k) for w in words):
                    hit = True
                    for w in words:
                        if w.startswith(k):
                            pos = min(pos, low.find(w))
                            break
                if hit:
                    weight = 1
                    break
        if weight:
            weights[cap] = (weight, pos)
    return weights


def score_candidates(reg, text, cap_map):
    """Rank every skill against the need. Capability overlap dominates."""
    cap_weights = infer_capabilities(text, cap_map)
    caps = set(cap_weights)
    # Where the capability was named in the request. The head of the
    # request carries the intent: in 'generate an illustration for the
    # landing page' the illustration is what is wanted, the landing page
    # is where it goes.
    cap_pos = {c: v[1] for c, v in cap_weights.items()}
    primary = set(c for c, v in cap_weights.items() if v[0] >= 2)
    secondary = set(c for c, v in cap_weights.items() if v[0] < 2)
    # When two capabilities are named at the same point in the request,
    # the more specific one is the intent: 'illustration' points at image
    # generation far more sharply than at design direction.
    holders = {}
    for s in all_skills(reg):
        for t in s.get("tags") or []:
            holders[t] = holders.get(t, 0) + 1
    head = min(primary, key=lambda c: (cap_pos[c], holders.get(c, 0))) if primary else None
    primary_weight = {c: (12 if c == head else 6) for c in primary}
    # A capability the request names in full is the intent. A capability
    # the request only names in passing is context: 'write unit tests for
    # the auth module' is a request for testing, and 'auth' is what the
    # tests are about. Adding the two together let a skill that happened
    # to cover both beat the skill that covered the intent.
    words = free_text(text)
    name_req = short_text(text)
    out = []
    for s in all_skills(reg):
        stags = set(s.get("tags") or [])
        p_hit = sum(1 for c in stags if c in primary)
        s_hit = len(secondary & stags)
        cap_overlap = (sum(primary_weight[c] for c in stags if c in primary)
                       + s_hit * 2)
        name_tokens = re.findall(
            r"[a-z0-9]+", ((s.get("name") or "") + " " + (s.get("slug") or "")).lower())
        name_words = set(_stem(x) for x in name_tokens)
        for t in name_tokens:
            for part in _compound_parts(t):
                name_words.add(part)
        desc_words = set(_stem(x) for x in re.findall(
            r"[a-z0-9]+", (s.get("description") or "").lower()))
        name_hits = sum(1 for w in name_req
                        if any(_words_match(w, x) for x in name_words))
        desc_hits = sum(1 for w in words
                        if any(_words_match(w, x) for x in desc_words))
        # One point each, and capped. The documented ranking is:
        # capability overlap dominates, then the ledger, then local over
        # marketplace, then cost. Word hits are only tie-breakers; giving
        # them more weight than that let a skill whose name happened to
        # contain a common word outrank the right capability.
        name_relevance = name_hits
        name_hits = min(name_hits, 3)
        desc_hits = min(desc_hits, 3)
        score = cap_overlap + name_hits + desc_hits
        if score <= 0:
            continue
        # A local skill is installed in the user's own root, a plugin skill
        # is installed but one layer away, a marketplace skill needs an
        # install first. When two cover the job, the more ready one wins.
        src = s.get("source")
        score += {"local": 5, "plugin": 4}.get(src, 1)
        # A skill you keep rejecting must not outrank one you accept.
        # The penalty is applied, not a ban: it stays reachable as a
        # last resort when nothing else covers the need.
        q = quality_of(s)
        if q < 0:
            # Capped. The ledger scores a reject at -50, which is far more
            # than any single routing signal, so an uncapped penalty would
            # bury the skill completely instead of demoting it.
            score -= min(abs(q) * 0.3, 15)
        item = dict(s)
        item["_match"] = score
        item["_caps"] = sorted(caps & stags)
        item["_cap_overlap"] = cap_overlap
        item["_full_cover"] = p_hit > 0
        item["_name_hits"] = name_hits
        item["_desc_hits"] = desc_hits
        item["_name_relevance"] = name_relevance
        out.append(item)
    # Documented order: capability overlap dominates, then the ledger,
    # then local/plugin over marketplace, then cost. Name relevance sits
    # between the ledger and source: when two skills cover the request
    # equally well, the one whose NAME is about the request is the better
    # answer, and cost should not pick between them on its own.
    out.sort(key=lambda s: (
        -s["_match"],
        -quality_of(s),
        -s.get("_name_relevance", 0),
        {"local": 0, "plugin": 1}.get(s.get("source"), 2),
        tokens_of(s) if tokens_of(s) > 0 else 10 ** 9,
        s.get("name") or "",
    ))
    # A list, not a set: the JSON payload dumps this directly and a set
    # is not serializable.
    return out, sorted(caps)


def cost_gate(candidates, cheap_cap=None):
    """Drop skills that cost far more than a skill that matches as well.

    The old rule compared a skill against the cheapest full-cover candidate
    and threw away everything above a multiple of that price. That threw
    away the *better* match: a 3.5k-token skill that matched the request
    exactly died because a 64-token skill that matched it loosely was
    cheaper.

    The rule now is Pareto. A skill is too expensive only if some other
    skill matches at least as well and costs a fraction of it. A 9.8k-token
    skill for a 190-token job still loses, because the small skill matches
    at least as well.
    """
    if cheap_cap is None:
        return candidates
    scored = [c for c in candidates
              if c.get("_full_cover") and tokens_of(c) > 0]
    if not scored:
        return candidates
    kept = []
    for c in candidates:
        cost = tokens_of(c)
        if cost <= 0 or not c.get("_full_cover"):
            kept.append(c)
            continue
        match = c.get("_match", 0)
        cheaper_better = [tokens_of(o) for o in scored
                           if o.get("_match", 0) >= match]
        if cheaper_better and cost > min(cheaper_better) * cheap_cap:
            continue
        kept.append(c)
    return kept or candidates


def local_path_for(s):
    """Absolute path to a local skill's SKILL.md, if we know it."""
    p = s.get("path")
    if p and Path(p).is_file():
        return Path(p)
    name = s.get("slug") or slugify(s.get("name") or "")
    roots = [Path.home() / ".minimax" / "skills", Path.home() / ".agents" / "skills",
             Path.home() / ".claude" / "skills", Path.home() / ".codex" / "skills"]
    for r in roots:
        cand = r / name / "SKILL.md"
        if cand.is_file():
            return cand
    return None


def strip_frontmatter(text):
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].lstrip("\n")
    return text


def describe(chosen, caps, runner):
    name = chosen.get("name")
    src = chosen.get("source")
    loc = local_path_for(chosen)
    where = str(loc) if loc else ("marketplace - install required" if src == "marketplace" else "not on disk")
    lines = [
        f"CHOSEN : {name}",
        f"WHY    : matched capabilities [{', '.join(chosen.get('_caps') or []) or 'free-text only'}]"
        + (f"; request inferred [{', '.join(caps)}]" if caps else ""),
        f"COST   : {tokens_of(chosen)} tokens on invoke",
        f"QUALITY: {quality_of(chosen):+d}" if quality_of(chosen) else "QUALITY: unseen",
        f"SOURCE : {src}",
        f"PATH   : {where}",
    ]
    if runner:
        lines.append(f"ACTION : skill tool call -> skill(name=\"{name}\")")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("need", nargs="?", help="the job to be done, in plain language")
    ap.add_argument("--skill", help="invoke this skill by name instead of resolving")
    ap.add_argument("--load", action="store_true", help="print the skill body so it can be followed now")
    ap.add_argument("--json", action="store_true", help="machine readable output")
    ap.add_argument("--limit", type=int, default=8, help="candidates to show")
    ap.add_argument("--cheap-cap", type=float, default=4.0,
                    help="max cost multiple over the cheapest full-cover candidate (default 4)")
    ap.add_argument("--home", default=None)
    args = ap.parse_args()

    root = skill_root(args.home)
    reg = load_registry(root)
    cap_map = load_capabilities(root)

    if args.skill:
        chosen = resolve(reg, args.skill)
        if not chosen:
            print(f"not found: {args.skill}")
            return 1
        candidates = [chosen]
        caps = []
    else:
        if not args.need:
            ap.print_help()
            return 1
        candidates, caps = score_candidates(reg, args.need, cap_map)
        if not candidates:
            print("no match - author a skill or stage a marketplace install")
            return 1
        candidates = cost_gate(candidates, args.cheap_cap)
        chosen = candidates[0]

    if args.json:
        payload = {
            "chosen": {k: v for k, v in chosen.items() if not k.startswith("_")},
            "matched_capabilities": chosen.get("_caps") or [],
            "inferred_capabilities": caps,
            "path": str(local_path_for(chosen) or ""),
            "candidates": [
                {"name": c.get("name"), "source": c.get("source"),
                 "tokens": tokens_of(c), "quality": quality_of(c),
                 "caps": c.get("_caps") or []}
                for c in candidates[:args.limit]
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(describe(chosen, caps, True))

    if args.load:
        loc = local_path_for(chosen)
        if not loc:
            print("\nNO BODY - this skill is not on disk locally; stage the install first.")
            return 2
        print("\n" + "=" * 68)
        print(strip_frontmatter(loc.read_text(encoding="utf-8")))
    else:
        print("\nALTERNATIVES:")
        for c in candidates[1:args.limit]:
            print(f"  {c.get('name'):<40} {tokens_of(c):>6}tok  "
                  f"{'local' if c.get('source') in ('local','plugin') else 'mkt'}  "
                  f"caps={','.join(c.get('_caps') or []) or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
