#!/usr/bin/env python3
"""resume: a bounded start-of-session briefing for a Myco Notes tree (Python standard library only).

Prints, as Markdown and within a character budget, what a session needs before it touches anything (method 11.2):
the repository state, the pending list of the sync tracker, the headline facts and open items of the master record, notes past a review
or follow-up date, the newest handoff and the last commits. It reads; it never writes. It is a view, not a source: open the note for any fact
you will act on.

Budget: --budget N characters (default 6000; RESUME_BUDGET in .myco-notes.conf). Items are kept whole or left out, never cut in half; the
footer says how many were left out. Sections are filled in the order above, so a long pending list cannot push out the review dates.

Usage:  tools/resume [--root DIR] [--budget N] [--today YYYY-MM-DD]
"""
import argparse, datetime, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import _myco  # noqa: E402
import notes_graph  # noqa: E402

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def section_items(text, heading_re, cap):
    """Bullet or numbered items under the first heading matching heading_re, up to the next heading of the same or a higher level.
    Indented continuation lines stay with their item; template placeholders such as '_(item)_' are skipped."""
    lines, items, level, inside = text.split("\n"), [], 0, False
    for line in lines:
        hm = re.match(r"^(#{1,6})\s+(.*)$", line)
        if hm:
            if inside and len(hm.group(1)) <= level:
                break
            if not inside and re.search(heading_re, hm.group(2), re.I):
                inside, level = True, len(hm.group(1))
            continue
        if not inside:
            continue
        if re.match(r"^\s{0,3}([-*]|\d+[.)])\s+\S", line):
            items.append(re.sub(r"\s+", " ", line.strip()))
        elif items and line.startswith((" ", "\t")) and line.strip():
            items[-1] += " " + line.strip()
    return [i for i in items if "_(" not in i][:cap]


def newest_handoff(root, notes):
    """The most recently committed file in <notes>/plans/ that looks like a handoff or session summary."""
    plans = notes + "/plans"
    d = os.path.join(root, *plans.split("/"))
    if not os.path.isdir(d):
        return None
    best = None
    for f in sorted(os.listdir(d)):
        if not f.endswith(".md") or not re.search(r"session|handoff|hand-off", f, re.I):
            continue
        rel = plans + "/" + f
        r = _myco.git("-C", root, "log", "-1", "--format=%ct", "--", rel)
        ts = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else int(os.path.getmtime(os.path.join(d, f)))
        if best is None or ts > best[0]:
            best = (ts, rel)
    if not best:
        return None
    text = open(os.path.join(root, *best[1].split("/")), encoding="utf-8", errors="replace").read()
    fm, fm_end = _myco.frontmatter(text)
    body = "\n".join(text.split("\n")[fm_end:])
    h1 = re.search(r"^#\s+(.+)$", body, re.M)
    status = next((re.sub(r"\s+", " ", l.strip())[:200] for l in body.split("\n") if re.search(r"merge status", l, re.I)), "")
    day = datetime.datetime.fromtimestamp(best[0], datetime.timezone.utc).strftime("%Y-%m-%d")
    item = f"`{best[1]}` ({day}): {re.sub(r'[`*_]', '', h1.group(1)).strip() if h1 else os.path.basename(best[1])}"
    return [item + (f" - {status}" if status else "")]


def main():
    _myco.utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--budget", type=int, default=None, help="character budget (default 6000)")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD, for repeatable output")
    a = ap.parse_args()
    root = _myco.toplevel(a.root) or os.path.abspath(a.root)
    conf = _myco.load_conf(root)
    lay = _myco.layout(root, conf)
    try:
        budget = a.budget if a.budget else int(conf.get("RESUME_BUDGET") or 6000)
    except ValueError:
        budget = 6000
    budget = max(budget, 600)
    today = a.today or datetime.date.today().isoformat()
    notes = lay["notes"]

    def read(rel):
        p = os.path.join(root, *rel.split("/"))
        return open(p, encoding="utf-8", errors="replace").read() if os.path.isfile(p) else ""

    entry = next((f for f in ("AGENTS.md", "CLAUDE.md", "README.md") if os.path.isfile(os.path.join(root, f))), None)
    first = re.search(r"^#\s+(.+)$", read(entry), re.M) if entry else None
    project = re.split(r"\s*:\s", first.group(1))[0].strip() if first else os.path.basename(root)

    # --- repository state
    state = []
    br = _myco.git("-C", root, "rev-parse", "--abbrev-ref", "HEAD")
    head = _myco.git("-C", root, "log", "-1", "--format=%h|%ad|%s", "--date=short")
    if head.returncode == 0 and head.stdout.strip():
        h, d, subj = head.stdout.strip().split("|", 2)
        dirty = [l for l in _myco.git("-C", root, "status", "--porcelain").stdout.split("\n") if l.strip()]
        state.append(f"branch {br.stdout.strip()}, last commit {h} ({d}): {subj}")
        state.append("working tree: " + (f"{len(dirty)} uncommitted change(s), commit or review them" if dirty else "clean"))
    else:
        state.append("no commits yet")
    if entry:
        state.append(f"entry point `{entry}`; method `{lay['docs']}/METHODOLOGY.md`; tools `{lay['tools']}/`")

    # --- pending, headline, open items
    pending = section_items(read(notes + "/sync-tracker.md"), r"^pending", 30)
    st = read(notes + "/" + lay["state"])
    headline = section_items(st, r"^headline", 8)
    openi = section_items(st, r"open items", 10)

    # --- past review or follow-up dates
    due = []
    hist = tuple(notes + "/" + h + "/" for h in lay["history"])
    skip = {lay["map"], lay["docs"], lay["tools"], *lay["data"]}
    for dp, dn, fn in os.walk(os.path.join(root, *notes.split("/"))):
        reldir = os.path.relpath(dp, root).replace(os.sep, "/")
        dn[:] = sorted(x for x in dn if x != ".git" and (reldir + "/" + x) not in skip)
        for f in sorted(fn):
            rel = reldir + "/" + f
            if not f.endswith(".md") or rel.startswith(hist):
                continue
            text = read(rel)
            fm, _ = _myco.frontmatter(text)
            why = []
            for k in ("review_after", "valid_until"):
                if k in fm and DATE.fullmatch(fm[k][0]) and fm[k][0] < today:
                    why.append(f"{k.replace('_', ' ')} {fm[k][0]}")
            n = notes_graph.due_count("\n".join(text.split("\n")[_myco.frontmatter(text)[1]:]), today)
            if n:
                why.append(f"{n} follow-up date(s) passed")
            if why:
                due.append(f"`{rel}`: " + "; ".join(why))
    handoff = newest_handoff(root, notes) or []
    log = _myco.git("-C", root, "log", "-5", "--format=%h %ad %s", "--date=short")
    recent = [l.strip() for l in log.stdout.split("\n") if l.strip()] if log.returncode == 0 else []

    sections = [("Repository", state), ("Pending (sync tracker)", pending), ("Headline facts (master record)", headline),
                ("Open items (master record)", openi), ("Past a review or follow-up date", due), ("Newest handoff", handoff),
                ("Last commits", recent)]
    head_txt = f"# {project}: resume, {today}\n"
    reserve = 190
    out, used, left = [head_txt], len(head_txt), 0
    for title, items in sections:
        if not items:
            continue
        block, cost = [], len(f"\n## {title}\n")
        for it in items:
            line = "- " + it + "\n"
            if used + cost + len(line) + reserve > budget:
                break
            block.append(line)
            cost += len(line)
        left += len(items) - len(block)
        if block:
            out.append(f"\n## {title}\n" + "".join(block))
            used += cost
    foot = f"\n---\nbudget {budget} characters; left out: {left} item(s)" + (f" (run with a larger --budget, or read `{notes}/` directly)" if left else "") + "\n"
    sys.stdout.write("".join(out) + foot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
