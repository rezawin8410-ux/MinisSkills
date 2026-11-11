"""notes_graph: the data and the page for the mycelium view (graph.html), called by build-notes-map.py.

The view is one self-contained HTML file (no server, no network, no libraries): notes are nodes, mentions between notes are hyphae, the
most-mentioned notes of the project are its hubs, and a timeline replays the git history so the network grows commit by commit. Only titles, paths, counts
and commit subjects are written into it, never the text of a note.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _myco  # noqa: E402
MAX_COMMITS = 2000
DUE = re.compile(r"\b(after|about|due|review-by|waiting until|on or after)\s+(\d{4}-\d{2}-\d{2})\b")
ILLUSTRATIVE = " *<>{}$|()[]"


def due_count(text, today):
    """Follow-up dates that have passed on lines not marked DONE (the same heuristic as notes-lint), plus a passed review_after or valid_until in the header."""
    n, in_code = 0, False
    fm, _ = _myco.frontmatter(text)
    n += sum(1 for k in ("review_after", "valid_until") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", fm.get(k, ("",))[0]) and fm[k][0] < today)
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or "DONE" in line or "~~" in line or re.match(r"^\s*(- \*\*|\d+\.\s+\*\*)\s*\d{4}-\d{2}-\d{2}", line):
            continue
        n += sum(1 for m in DUE.finditer(line) if m.group(2) < today)
    return n


def missing_refs(root, rel, text, names, skip_prefixes):
    """Backticked note names (`x.md`) that exist nowhere: the same references notes-lint reports as missing."""
    if rel.startswith(skip_prefixes):
        return []
    out, in_code = [], False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        for tok in re.findall(r"`([^`\n]+)`", line):
            tok = tok.lstrip("@")
            if not tok.endswith(".md") or any(c in tok for c in ILLUSTRATIVE) or tok.startswith(("/", "~", "http", "./", "../", ".")):
                continue
            if tok in names or os.path.basename(tok) in names or os.path.exists(os.path.join(root, *tok.split("/"))) \
                    or os.path.exists(os.path.join(root, *os.path.dirname(rel).split("/"), *tok.split("/"))):
                continue
            if tok not in out:
                out.append(tok)
    return out


def commit_history(root, rels, sh):
    """Chronological commits that touched notes: [{"t", "s", "n": [paths touched], "a": [paths added]}] with paths mapped to today's names."""
    raw = sh(["git", "-c", "core.quotepath=off", "log", "-M", "--name-status", f"-n{MAX_COMMITS}", "--format=\x01%h\t%ct\t%s", "--", "*.md"], root)
    ident, commits = {}, []
    for block in raw.split("\x01")[1:]:          # newest first, so renames are resolved before the older commits that used the old name
        lines = block.split("\n")
        head = lines[0].split("\t", 2)
        if len(head) < 3:
            continue
        touched, added = [], []
        for ln in lines[1:]:
            parts = ln.split("\t")
            st = parts[0][:1]
            if st == "R" and len(parts) == 3:
                final = ident.get(parts[2], parts[2])
                ident[parts[1]] = final
                touched.append(final)
            elif st in ("A", "M") and len(parts) == 2:
                final = ident.get(parts[1], parts[1])
                touched.append(final)
                if st == "A":
                    added.append(final)
        commits.append({"t": int(head[1]) if head[1].isdigit() else 0, "s": head[2][:110],
                        "n": sorted({p for p in touched if p in rels}), "a": sorted({p for p in added if p in rels})})
    commits.reverse()
    return commits


def build(root, lay, info, groups, out, title, sh, today, built):
    """Write <out>/graph.html. `info` is the map builder's table of notes (rel -> id, title, group, out, inn, size)."""
    rels = sorted(info)
    index = {rel: i for i, rel in enumerate(rels)}
    commits = commit_history(root, set(rels), sh)
    born, last = {}, {}
    for ci, c in enumerate(commits):
        for p in c["n"]:
            born.setdefault(p, ci)
            last[p] = ci
    uncommitted = max(len(commits) - 1, 0)
    names = {d["name"] for d in info.values()}
    skip_prefixes = (lay["docs"] + "/", "template/", lay["map"] + "/") + tuple(lay["notes"] + "/" + h + "/" for h in lay["history"])
    hist_prefixes = tuple(lay["notes"] + "/" + h + "/" for h in lay["history"])
    gids = [g[0] for g in groups]
    hubs = hub_set(info, hist_prefixes)
    nodes = []
    for rel in rels:
        d = info[rel]
        n = {"id": d["id"], "rel": rel, "name": d["name"], "title": d["title"][:90], "g": gids.index(d["group"]) if d["group"] in gids else 0,
             "kb": round(d["size"] / 1024, 1), "in": len(d["inn"]), "out": len(d["out"]), "hub": rel in hubs, "hist": rel.startswith(hist_prefixes)}
        n["b"] = born.get(rel, uncommitted) if commits else -1
        if rel in last:
            n["l"] = last[rel]
        due = 0 if n["hist"] else due_count(d["text"], today)
        if due:
            n["due"] = due
        miss = [] if n["hist"] else missing_refs(root, rel, d["text"], names, skip_prefixes)
        if miss:
            n["miss"] = miss[:4] + (["+%d more" % (len(miss) - 4)] if len(miss) > 4 else [])
        nodes.append(n)
    byid = {d["id"]: rel for rel, d in info.items()}
    pair = {}
    for rel, d in info.items():
        for tid, w in d["out"].items():
            a, b = index[rel], index[byid[tid]]
            key = (min(a, b), max(a, b))
            cur = pair.setdefault(key, [0, 0])
            cur[0 if a < b else 1] += w
    edges = [[a, b, w1, w2] for (a, b), (w1, w2) in sorted(pair.items())]
    data = {"title": title, "built": built, "groups": [], "nodes": nodes, "edges": edges, "commits": [{"t": c["t"], "s": c["s"], "n": [index[p] for p in c["n"]], "a": [index[p] for p in c["a"]]} for c in commits]}
    used = sorted({n["g"] for n in nodes})                       # keep only the groups that have notes, renumbered
    remap = {g: i for i, g in enumerate(used)}
    data["groups"] = [{"id": groups[g][0], "name": groups[g][1]} for g in used]
    for n in nodes:
        n["g"] = remap[n["g"]]
    blob = json.dumps(data, ensure_ascii=True, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\!--")
    with open(os.path.join(HERE, "graph-template.html"), encoding="utf-8") as fh:
        page = fh.read()
    links = "".join(f'<li><a href="p/{n["id"]}.html">{_esc(n["title"])} ({_esc(n["rel"])})</a></li>' for n in nodes)
    page = page.replace("%%TITLE%%", _esc(title)).replace("%%LINKS%%", links).replace("%%DATA%%", blob)
    with open(os.path.join(out, "graph.html"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    return len(nodes), len(edges), len(commits)


def hub_set(info, hist_prefixes):
    """The notes that most other notes mention: a few per project, chosen by the mentions. Every tree has the same entry and method notes
    (the "start" and "docs" groups), so highlighting those would say nothing about this project: they are never hubs, nor are history
    notes. A note needs at least two mentions to count."""
    live = [r for r in info if not r.startswith(hist_prefixes) and info[r]["group"] not in ("start", "docs") and len(info[r]["inn"]) >= 2]
    k = min(6, max(2, len(info) // 8))
    return set(sorted(live, key=lambda r: (-len(info[r]["inn"]), -info[r]["size"], r))[:k])


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
