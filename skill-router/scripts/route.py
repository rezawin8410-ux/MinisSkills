#!/usr/bin/env python3
"""
route.py — pick the right skill for an intent, in one command.

An agent with six skills in front of it does what a human does with six remote
controls: picks the wrong one. The fix is not a better description, it is a
router that reads the descriptions for you.

It reads what the pack already declares — `MANIFEST.json` when present, the YAML
frontmatter of every `SKILL.md` otherwise — and scores an intent against it:

    score = 3.0 x (phrase match in use_when)
          + 3.0 x (curated keyword hit rate)      <- the signal that actually works
          + 1.5 x (keyword match in description)
          + 1.5 x (keyword match in skill name)
          - 4.0 x (match in not_for)              <- a veto, not a nudge

    route.py "make me a logo for a coffee shop"
    route.py "how do I fix this traceback" --json
    route.py "read this page and summarise it" --top 3
    route.py --list                      # the whole routing table
    route.py "voiceover" --explain       # show the scoring, not just the answer

Output is a command you can paste, not a recommendation you have to interpret.

Exit codes: 0 match found · 1 nothing above threshold · 2 no skills found
"""
import argparse
import json
import os
import re
import sys

STOP = set("""a an the and or but if then than that this these those of in on at to for from
with without into over under i me my we our you your it its is are was were be been being
do does did can could should would will shall may might must have has had please need needs
needed want wants wanted give get make me some any how what when where which who why
something anything there here about like just also very really help""".split())

THRESHOLD = 1.00      # below this we say 'no confident match' instead of guessing


# ------------------------------------------------------------------- discovery
def find_root(explicit=None):
    for cand in [explicit, os.environ.get("AGENT_SKILLS_DIR")]:
        if cand and os.path.isdir(cand):
            return os.path.abspath(cand)
    here = os.path.abspath(os.path.dirname(__file__))
    for up in (here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))):
        if os.path.exists(os.path.join(up, "MANIFEST.json")):
            return up
        if any(os.path.isdir(os.path.join(up, d)) and
               os.path.exists(os.path.join(up, d, "SKILL.md")) for d in os.listdir(up)
               if not d.startswith(".")):
            return up
    cwd = os.getcwd()
    for cand in (cwd, os.path.dirname(cwd)):
        if os.path.exists(os.path.join(cand, "MANIFEST.json")):
            return cand
    return None


def frontmatter(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}
    out, key = {}, None
    for line in m.group(1).splitlines():
        if re.match(r"^[A-Za-z_]+:", line):
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip()
            out[key] = val
        elif key and line.startswith((" ", "-", "\t")):
            out[key] = (out.get(key, "") + " " + line.strip(" -")).strip()
    return out


def _slurp(path):
    """Read a small text file without leaking the handle."""
    with open(path, encoding="utf-8") as f:
        return f.read()


def dir_skills(root):
    """Fallback: derive the catalogue from SKILL.md frontmatter alone."""
    skills = []
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name, "SKILL.md")
        if not os.path.isfile(p):
            continue
        fm = frontmatter(_slurp(p))
        scripts = os.path.join(root, name, "scripts")
        entry = None
        if os.path.isdir(scripts):
            for f in sorted(os.listdir(scripts)):
                if f.endswith(".py") and not f.startswith("_"):
                    entry = f"{name}/scripts/{f}"
                    break
        if not entry:
            continue
        skills.append({"name": fm.get("name") or name, "path": name, "entry": entry,
                       "description": fm.get("description", ""),
                       "use_when": [fm["description"]] if fm.get("description") else [],
                       "not_for": [], "commands": []})
    return skills


def catalogue(root):
    man = os.path.join(root, "MANIFEST.json")
    if os.path.isfile(man):
        try:
            with open(man, encoding="utf-8") as f:
                d = json.load(f)
            skills = []
            for s in d.get("skills", []):
                s = dict(s)
                s.setdefault("description", "")
                if not s.get("description"):
                    p = os.path.join(root, s["path"], "SKILL.md")
                    if os.path.isfile(p):
                        s["description"] = frontmatter(_slurp(p)).get(
                            "description", "")
                skills.append(s)
            if skills:
                return skills
        except Exception:
            pass
    return dir_skills(root)


# -------------------------------------------------------------------- scoring
def tokens(text):
    return {w for w in re.findall(r"[a-z0-9][a-z0-9+#.\-]*", (text or "").lower())
            if w not in STOP and len(w) > 1}


def phrase_hit(intent, phrase):
    """Loosely: does the intent look like the situations the phrase describes?"""
    iw, pw = tokens(intent), tokens(phrase)
    if not iw or not pw:
        return 0.0
    overlap = iw & pw
    # a phrase hit means the intent covers a decent part of the phrase's gist,
    # not that they share an article
    return len(overlap) / max(3.0, min(len(pw), 8))


META = re.compile(r"\b(which|what)\s+(skill|tool|command|script|entry ?point)s?\b"
                  r"|\bpick (a|the) skill\b|\bwhat should i use\b|\blist (the )?skills\b", re.I)


def is_meta(intent):
    """'which skill should I use' is not a task, it is a question about the pack.
    Bag-of-words scoring will never rank the router first for it, so say so."""
    return bool(META.search(intent))


def keyword_hit(intent, keywords):
    """Curated keywords beat prose. 'traceback' is worth more than the fact that
    a description happens to contain the word 'question'."""
    iw = tokens(intent)
    kws = [k for k in (keywords or []) if k]
    if not iw or not kws:
        return 0.0
    hits = 0.0
    for k in kws:
        kw_tokens = tokens(k)
        if kw_tokens and kw_tokens <= iw:
            hits += 1.0 + 0.5 * (len(kw_tokens) - 1)   # multi-word keys are sharper
    return min(1.0, hits / 2.0)          # two keyword hits = full credit


def score_skill(intent, skill):
    detail = {"use_when": 0.0, "keywords": 0.0, "description": 0.0,
              "name": 0.0, "not_for": 0.0}
    best_phrase = 0.0
    for ph in skill.get("use_when") or []:
        best_phrase = max(best_phrase, phrase_hit(intent, ph))
    detail["use_when"] = 3.0 * min(best_phrase, 1.0)

    detail["keywords"] = 3.0 * keyword_hit(intent, skill.get("keywords"))

    iw = tokens(intent)
    dw = tokens(skill.get("description", ""))
    if iw and dw:
        detail["description"] = 1.5 * len(iw & dw) / max(4.0, min(len(iw), 10))

    nw = tokens(skill.get("name", "").replace("-", " "))
    if iw and nw:
        detail["name"] = 1.5 * len(iw & nw) / max(2.0, len(nw))

    for ph in skill.get("not_for") or []:
        if phrase_hit(intent, ph) >= 0.5 or tokens(ph) <= tokens(intent):
            detail["not_for"] = -4.0
            break

    return sum(detail.values()), detail


def hint_command(skill, intent):
    """Turn the manifest entry into something runnable."""
    entry = skill.get("entry", "")
    name = skill.get("name", "")
    cmds = skill.get("commands") or []
    q = re.sub(r"\s+", " ", intent).strip().strip('"')
    if name == "anon-image":
        return f'python3 {entry} gen "{q}" -o out.jpg'
    if name == "anon-audio":
        return f'python3 {entry} say "{q}" -o out.mp3'
    if name == "ffmpeg-pipeline":
        return f"python3 {entry} --help   # then: video / slideshow / mux / gif / …"
    if name == "web-search":
        return f'python3 {entry} "{q}" --limit 10'
    if name == "fetch-page":
        return f"python3 {entry} <URL> --text"
    if name == "deep-research":
        return f'python3 {entry} "{q}" -o report.md --depth 2'
    if name == "skill-router":
        return f"python3 {entry} --list"
    return f"python3 {entry} {' '.join(cmds[:2])}".strip()


def main():
    ap = argparse.ArgumentParser(description="route an intent to the right skill")
    ap.add_argument("intent", nargs="*", help="what you want to do, in plain words")
    ap.add_argument("--skills-dir", default=None)
    ap.add_argument("--top", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--explain", action="store_true", help="show the score breakdown")
    ap.add_argument("--list", action="store_true", help="print the routing table")
    a = ap.parse_args()

    root = find_root(a.skills_dir)
    if not root:
        print("no skills found: pass --skills-dir or set AGENT_SKILLS_DIR", file=sys.stderr)
        return 2
    skills = catalogue(root)
    if not skills:
        print(f"no skills with a SKILL.md under {root}", file=sys.stderr)
        return 2
    if len(skills) == 1 and not a.list:
        print(f"only one skill found under {root} — nothing to route between.\n"
              f"pass --skills-dir /path/to/pack, or set AGENT_SKILLS_DIR",
              file=sys.stderr)
        return 2

    if a.list:
        print(f"{root}\n")
        for s in skills:
            first = (s.get("use_when") or [""])[0]
            print(f"{s['name']:<16}{s.get('entry', ''):<40}{first[:70]}")
        print(f"\n{len(skills)} skills")
        return 0

    intent = " ".join(a.intent).strip()
    if not intent:
        ap.error("give me an intent, e.g.  route.py \"make me a logo\"")

    ranked = []
    for s in skills:
        sc, detail = score_skill(intent, s)
        ranked.append((sc, s, detail))
    if is_meta(intent):
        for i, (sc, s_, _) in enumerate(ranked):
            if s_["name"] == "skill-router":
                ranked.insert(0, ranked.pop(i))
                break
    ranked.sort(key=lambda x: -x[0]) if not is_meta(intent) else None
    top = ranked[:max(1, a.top)]

    if a.json:
        print(json.dumps({
            "intent": intent,
            "root": root,
            "match": None if ranked[0][0] < THRESHOLD else {
                "skill": top[0][1]["name"], "entry": top[0][1].get("entry"),
                "command": hint_command(top[0][1], intent),
                "score": round(top[0][0], 3)},
            "alternatives": [{"skill": s["name"], "score": round(sc, 3)} for sc, s, _ in top[1:]],
            "all": [{"skill": s["name"], "score": round(sc, 3)} for sc, s, _ in ranked],
        }, indent=1))
        return 0 if ranked[0][0] >= THRESHOLD else 1

    if ranked[0][0] < THRESHOLD:
        print(f"no confident match for: {intent!r}")
        print("closest:")
        for sc, s, _ in top:
            print(f"  {s['name']:<16}{sc:.2f}  {hint_command(s, intent)}")
        print("\ntry --list, or say what you actually want in plainer words")
        return 1

    sc, best, detail = ranked[0]
    print(f"{best['name']}  ({sc:.2f})")
    print(f"  {hint_command(best, intent)}")
    if a.explain:
        for k, v in detail.items():
            print(f"    {k:<12}{v:+.2f}")
    if a.top > 1:
        second = ranked[1]
        if second[0] >= sc - 1.2 and second[1]["name"] in (best.get("composes_with") or []):
            print(f"\n  note: close call with {second[1]['name']} and it composes — "
                  f"{best['name']} can call it for you")
        print("\nalternatives:")
        for sc2, s2, d2 in ranked[1:a.top]:
            print(f"  {s2['name']:<16}{sc2:.2f}  {hint_command(s2, intent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
