#!/usr/bin/env python3
"""Audit the capability taxonomy and propose improvements.

The registry's discovery and scoring are automatic: new skills are found on a
rebuild, and accept/reject writes a score that changes ranking. The taxonomy
itself, though, was only ever improved by hand. This is the missing half.

It reads the registry, the taxonomy and the ledger, and proposes concrete
edits to registry/capabilities.json. Read-only. It never edits the taxonomy.

Usage:
    python taxonomy-audit.py              # proposals, human readable
    python taxonomy-audit.py --json       # machine readable
    python taxonomy-audit.py --only gaps  # one section
"""

import argparse
import json
import re
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

GENERIC_STEMS = {"skill", "readme", "license", "index", "package", "changelog", "main", "common"}

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def skill_text(skill):
    """Description plus frontmatter: the same text the tagger reads.

    The registry stores only the description. A skill like
    'systematic-debugging' declares its domain in the frontmatter, not in
    the prose, and an audit that only reads the prose calls that tag
    stale when the tag is correct.
    """
    p = skill.get("path")
    if not p:
        return (skill.get("description") or "").lower()
    try:
        raw = Path(p).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return (skill.get("description") or "").lower()
    m = FM_RE.match(raw)
    if not m:
        return (skill.get("description") or "").lower()
    return m.group(1).lower()


def name_token_match(name_low, keyword):
    """Weak keyword vs a skill NAME, matched per name token."""
    kw = (keyword or "").lower().strip()
    if not kw:
        return False
    if " " in kw or "-" in kw:
        return kw in name_low
    if len(kw) < 3:
        return False
    for tok in re.split(r"[^a-z0-9]+", name_low):
        if tok.startswith(kw):
            return True
    return False


def name_token_exact(name_low, keyword):
    """A weak keyword that must match a whole name token."""
    kw = (keyword or "").lower().strip()
    if not kw:
        return False
    return kw in re.split(r"[^a-z0-9]+", name_low)


# --- shared router state -------------------------------------------------
# The router is installed in more than one skills root. The mutable state
# - the registry index and the outcome ledger - must have exactly one copy,
# or the two installs drift and the router ranks differently depending on
# who is asking. Shared location: ~/.minimax/local-runtime/skill-learning/
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
    return shared_state_dir() / 'skills.json'


def shared_ledger_path():
    return shared_state_dir() / 'ledger.json'


def skill_root(home=None):
    home = Path(home) if home else Path.home()
    for base in (home / ".agents" / "skills", home / ".claude" / "skills"):
        p = base / "aswath-capability"
        if p.is_dir():
            return p
    raise SystemExit("aswath-capability skill not found")


def load(root, name):
    p = shared_state_dir() / name
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def stems_of(skill):
    """Re-derive the reference-file stems for a skill from its path."""
    p = skill.get("path")
    if not p:
        return set()
    d = Path(p).parent
    out = set()
    if not d.is_dir():
        return out
    for f in d.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() not in (".md", ".json", ".toml", ".yaml", ".yml", ".txt"):
            continue
        stem = f.stem.lower()
        if stem not in GENERIC_STEMS:
            out.add(stem)
    return out


def words(text):
    return set(re.findall(r"[a-z0-9\-]{4,}", (text or "").lower()))


def suggest_for_stems(stems, capabilities):
    """Which capability does this skill's own reference files point at?

    This is the same reasoning a human applies when they read the folder
    listing: a skill shipping critique.md critiques. Automating it is what
    lets the taxonomy keep up with new skills arriving.
    """
    hits = defaultdict(list)
    for cap, spec in capabilities.items():
        if not isinstance(spec, dict):
            continue
        for kw in spec.get("files") or []:
            k = kw.lower()
            for stem in stems:
                if k == stem or stem.startswith(k + "-") or k.startswith(stem + "-"):
                    hits[cap].append(stem)
    return {cap: sorted(set(v)) for cap, v in hits.items()}


def audit(root):
    reg = load(root, "skills.json")
    caps = load(root, "capabilities.json").get("capabilities", {})
    ledger = load(root, "ledger.json").get("skills", {})

    local = reg.get("local", [])
    plugin = reg.get("plugin", [])
    proposals = {
        "unroutable": [],
        "stale_tags": [],
        "rejected_but_routed": [],
        "starved": [],
        "over_broad": [],
    }

    # 1. Skills nothing can reach.
    for s in local + plugin:
        if s.get("router"):
            continue
        if s.get("tags"):
            continue
        if s.get("tokens_est", 0) < 120:
            continue  # too thin to be worth routing
        stems = stems_of(s)
        sug = suggest_for_stems(stems, caps)
        proposals["unroutable"].append({
            "skill": s["name"],
            "tokens": s.get("tokens_est"),
            "suggested": sug,
            "desc_words": sorted(words(s.get("description")))[:12],
        })

    # 2. Tags the evidence does not support.
    for s in local + plugin:
        if s.get("router"):
            continue
        text = skill_text(s)
        stems = stems_of(s)
        name = (s.get("name") or "").lower()
        for cap in s.get("tags") or []:
            spec = caps.get(cap) or {}
            strong = [k.lower() for k in (spec.get("strong") or [])]
            weak = [k.lower() for k in (spec.get("weak") or [])]
            files = [k.lower() for k in (spec.get("files") or [])]
            weak_exact = [k.lower() for k in (spec.get("weak_exact") or [])]
            desc_hit = any(k in text for k in strong)
            file_hit = any(k in stems for k in files)
            name_hit = any(name_token_match(name, k) for k in weak) or \
                any(name_token_exact(name, k) for k in weak_exact)
            if not (desc_hit or file_hit or name_hit):
                proposals["stale_tags"].append({"skill": s["name"], "capability": cap})

    # 3. Skills you keep rejecting but routing keeps selecting.
    for name, rec in ledger.items():
        a, r = rec.get("accepts", 0), rec.get("rejects", 0)
        if r == 0 or a > r:
            continue
        match = next((s for s in local + plugin if s["name"] == name), None)
        if not match:
            continue
        proposals["rejected_but_routed"].append({
            "skill": name,
            "score": rec.get("score", 0),
            "accepts": a, "rejects": r,
            "capabilities": match.get("tags") or [],
            "note": (rec.get("notes") or [{}])[-1].get("note", ""),
        })

    # 4. Capabilities nothing on this machine provides.
    provided = Counter()
    for s in local + plugin:
        for t in s.get("tags") or []:
            provided[t] += 1
    for cap in caps:
        if provided.get(cap, 0) == 0:
            proposals["starved"].append({"capability": cap})

    # 5. Capabilities held by many skills through a weak signal only.
    #
    # The useful question is not "how many holders" but "which one weak
    # keyword produced them". A capability held by 12 correct skills is
    # fine; a capability held by 12 skills because 'cloud' matched
    # 'cloudcart' is a loose keyword and must be fixed.
    for cap, spec in caps.items():
        weak = [k.lower() for k in (spec.get("weak") or [])] if isinstance(spec, dict) else []
        weak_exact = [k.lower() for k in (spec.get("weak_exact") or [])] if isinstance(spec, dict) else []
        if not weak and not weak_exact:
            continue
        holders = {}
        total = 0
        for s in local + plugin:
            if cap not in (s.get("tags") or []):
                continue
            total += 1
            text = skill_text(s)
            name = (s.get("name") or "").lower()
            stems = stems_of(s)
            strong_hit = any(k in text for k in [x.lower() for x in (spec.get("strong") or [])])
            file_hit = any(k in stems for k in [x.lower() for x in (spec.get("files") or [])])
            if strong_hit or file_hit:
                continue
            kw = next((k for k in weak if name_token_match(name, k)), None)
            if not kw:
                kw = next((k for k in weak_exact if name_token_exact(name, k)), None)
            if kw:
                holders.setdefault(kw, []).append(s["name"])
        # A capability held by 12 correct skills through a weak signal is
        # fine. It is over-broad when the weak signal is doing MOST of the
        # work: 'cloud' matched 'cloudcart', 'api' matched every app that
        # happens to expose an api. Then the keyword, not the evidence, is
        # making the decision.
        for kw, names in holders.items():
            if len(names) >= 4 and total and len(names) * 2 >= total:
                proposals["over_broad"].append({
                    "capability": cap, "weak_keyword": kw, "weak_only": names,
                    "holders_total": total})

    return proposals


def render(proposals):
    out = []
    out.append("=" * 72)
    out.append("TAXONOMY AUDIT - proposals only, nothing is written")
    out.append("=" * 72)

    u = proposals["unroutable"]
    out.append(f"\n[UNROUTABLE] {len(u)} skills nothing can reach")
    if u:
        out.append("  These can never be selected: no capability matched them.")
        for item in u[:15]:
            out.append(f"    {item['skill']}  ({item['tokens']} tok)")
            if item["suggested"]:
                for cap, stems in sorted(item["suggested"].items(), key=lambda kv: -len(kv[1]))[:2]:
                    out.append(f"       -> add capability '{cap}'  (ships {', '.join(stems[:4])})")
            else:
                out.append(f"       -> no stem match; consider a 'strong' keyword from: "
                           f"{', '.join(item['desc_words'][:8])}")
    else:
        out.append("  none")

    st = proposals["stale_tags"]
    out.append(f"\n[STALE TAGS] {len(st)} tags the evidence does not support")
    if st:
        for item in st[:12]:
            out.append(f"    {item['skill']} -> {item['capability']}")
        out.append("  -> confirm each, then drop the capability from that skill's tags")
    else:
        out.append("  none")

    r = proposals["rejected_but_routed"]
    out.append(f"\n[REJECTED BUT ROUTED] {len(r)} skills you keep rejecting")
    if r:
        for item in r:
            out.append(f"    {item['skill']}  score {item['score']:+d}  "
                       f"(a{item['accepts']}/r{item['rejects']})")
            out.append(f"       capabilities: {', '.join(item['capabilities']) or 'none'}")
            if item["note"]:
                out.append(f"       your note: {item['note']}")
        out.append("  -> narrow the capability that keeps selecting it, or rewrite its description")
    else:
        out.append("  none")

    sv = proposals["starved"]
    out.append(f"\n[STARVED CAPABILITIES] {len(sv)} with no local provider")
    if sv:
        out.append("  " + ", ".join(s["capability"] for s in sv))
        out.append("  -> author a skill for these, or stage a marketplace install")
    else:
        out.append("  none")

    ob = proposals["over_broad"]
    out.append(f"\n[OVER-BROAD] {len(ob)} capabilities held on a weak signal alone")
    if ob:
        for item in ob:
            out.append(f"    {item['capability']}: {', '.join(item['weak_only'])}")
        out.append("  -> tighten the keyword rule so these stop matching")
    else:
        out.append("  none")

    out.append("\n" + "-" * 72)
    out.append("Nothing was changed. Edit registry/capabilities.json, then:")
    out.append("    python scripts/build-registry.py")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", choices=["unroutable", "stale_tags", "rejected_but_routed", "starved", "over_broad"])
    args = ap.parse_args()

    root = skill_root(args.home)
    proposals = audit(root)
    if args.only:
        proposals = {args.only: proposals[args.only]}
    if args.json:
        print(json.dumps(proposals, indent=2, ensure_ascii=False))
    else:
        print(render(proposals))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
