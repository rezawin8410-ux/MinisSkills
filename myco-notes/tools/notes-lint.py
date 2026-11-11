#!/usr/bin/env python3
"""notes-lint: cheap consistency checks for a Myco Notes tree (Python standard library only).

Checks
  refs      backticked names of notes (*.md) and of repository paths that do not exist
  headings  duplicate numbered headings in one file ("## 9." twice)
  fresh     "Last updated <date>" header older than the newest changelog entry in the same file
  due       follow-up dates that have passed ("after 2026-11-04", "review-by 2026-12-01") on lines not marked DONE (heuristic; words: after, about, due, review-by, waiting until, on or after)
  ledger    sync-tracker rows without a last-synced date or a refresh method
  secrets   secret-looking strings (the rules of tools/secret_scan.py, the same as the pre-commit hook and CI; values are not shown)
  orphans   notes that no other note mentions by file name (info only)
  size      notes larger than MAX_NOTE_KB (default 100) and an AGENTS.md near or over the 32 KiB some agents load (warns from ENTRY_WARN_KB, default 24)
  header    the optional freshness header (method 10.1: last_confirmed, review_after, valid_from, valid_until, supersedes, superseded_by): malformed or
            reversed dates, a passed review_after or valid_until, last_confirmed older than STALE_DAYS (default 365) without a review_after, missing targets

Usage:  tools/notes-lint.py [--root DIR] [--quiet] [--strict]
Exit:   0 clean, 1 warnings, 2 errors (secrets), 3 the lint could not run (for example an invalid ignore expression). --strict turns
        warnings into exit 1 even for info-only runs of 'due'. The last line is always the 'notes-lint: N notes, ...' summary when it finished.
Ignore: put regular expressions (one per line) in .myco-notes-lint-ignore; a finding whose text matches is dropped.
Layout (.myco-notes.conf): the generated map folder and DATA_DIRS (project data that is not notes) are not read as notes; the folders in
        HISTORY_DIRS (default "archive logs", inside the notes folder) are records, so references, follow-up dates, size and orphans are not
        checked there (secrets always are).
"""
import argparse, datetime, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import _myco  # noqa: E402
import secret_scan  # noqa: E402

SKIP_DIRS = {".git", "site", "notes-map", "node_modules", "__pycache__"}
DATE = r"\d{4}-\d{2}-\d{2}"


def repo_root(start):
    try:
        return subprocess.run(["git", "-C", start, "rev-parse", "--show-toplevel"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout.strip()
    except Exception:
        return os.path.abspath(start)


def join_rel(rel, tok):
    """tok resolved against the folder of the note `rel`, as a repository path with forward slashes (also on Windows)."""
    return os.path.normpath(os.path.join(os.path.dirname(rel), tok)).replace(os.sep, "/")


def walk(root, skip_rel):
    """Yield (relative folder, file names), leaving out SKIP_DIRS by name and the folders in skip_rel by relative path."""
    for dp, dn, fn in os.walk(root):
        reldir = os.path.relpath(dp, root).replace(os.sep, "/")
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and (d if reldir == "." else reldir + "/" + d) not in skip_rel)
        yield reldir, sorted(fn)


def walk_md(root, skip_rel):
    return [(f if reldir == "." else reldir + "/" + f) for reldir, fn in walk(root, skip_rel) for f in fn if f.endswith(".md")]


def all_files(root, skip_rel):
    names, paths = set(), set()
    for reldir, fn in walk(root, skip_rel):
        for f in fn:
            names.add(f)
            paths.add(f if reldir == "." else reldir + "/" + f)
    return names, paths


def num(conf, key, default):
    try:
        return float(conf.get(key) or default)
    except ValueError:
        return float(default)


def main():
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--quiet", action="store_true", help="hide info lines")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    root = repo_root(a.root)
    conf = _myco.load_conf(root)
    lay = _myco.layout(root, conf)
    docs_prefix, map_prefix = lay["docs"] + "/", lay["map"] + "/"
    history = tuple(lay["notes"] + "/" + d + "/" for d in lay["history"])
    max_kb, entry_warn_kb = num(conf, "MAX_NOTE_KB", 100), num(conf, "ENTRY_WARN_KB", 24)
    stale_days = num(conf, "STALE_DAYS", 365)
    ignore = []
    ip = os.path.join(root, ".myco-notes-lint-ignore")
    if os.path.exists(ip):
        with open(ip, encoding="utf-8") as fh:
            ignore = secret_scan.compile_allow([l.strip() for l in fh if l.strip() and not l.startswith("#")], ".myco-notes-lint-ignore")
    today = datetime.date.today()
    md_files = walk_md(root, {lay["map"], *lay["data"]})
    names, paths = all_files(root, {lay["map"]})
    top_dirs = {p.split("/")[0] for p in paths if "/" in p}
    findings = []   # (level, file, line, text)

    def add(level, f, line, text):
        s = f"{f}:{line} {text}"
        if any(r.search(s) for r in ignore):
            return
        findings.append((level, f, line, text))

    texts = {}
    mentions = {f: 0 for f in md_files}
    for rel in md_files:
        texts[rel] = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read()

    for rel, text in texts.items():
        lines = text.split("\n")
        in_code = False
        is_hist = rel.startswith(history)
        kb = len(text.encode("utf-8")) / 1024
        if rel == "AGENTS.md" and kb > 32:
            add("WARN", rel, 1, f"{kb:.0f} KB is over the 32 KiB that Codex and some other agents load; the rest is silently cut off. Move detail into notes and keep this file short")
        elif rel == "AGENTS.md" and kb > entry_warn_kb:
            add("WARN", rel, 1, f"{kb:.0f} KB is close to the 32 KiB some agents load (Codex); keep this file short and point to notes")
        elif not is_hist and kb > max_kb:
            add("WARN", rel, 1, f"{kb:.0f} KB is large (limit {max_kb:.0f}): keep current state here and move narrative to logs or the archive (method, section 3.3)")
        # refs, due, secrets, headings
        seen_heads = {}
        for i, line in enumerate(lines, 1):
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            hit = secret_scan.find(line)
            if hit:
                add("ERROR", rel, i, f"secret-looking string ({hit[0]}; remove it or store it where the notes say secrets live)")
            if in_code:
                continue
            for tok in ([] if is_hist else re.findall(r"`([^`\n]+)`", line)):
                tok = tok.lstrip("@")
                if any(c in tok for c in " *<>{}$|()[]") or tok.startswith(("/", "~", "http", "./", "../", ".")):
                    continue
                if rel.startswith((docs_prefix, "template/")) or tok.startswith(map_prefix):
                    continue                      # method documentation and templates use illustrative names; the map is generated
                if tok.endswith(".md"):
                    base = os.path.basename(tok)
                    ok = (tok in paths or join_rel(rel, tok) in paths
                          or (base == tok and base in names))
                    if ok:
                        for other in md_files:
                            if os.path.basename(other) == base and other != rel:
                                mentions[other] += 1
                    else:
                        add("WARN", rel, i, f"reference to missing note `{tok}`")
                elif "/" in tok and tok.split("/")[0] in top_dirs and re.search(r"\.[A-Za-z0-9]{1,5}$", tok):
                    if tok not in paths and join_rel(rel, tok) not in paths:
                        add("WARN", rel, i, f"reference to missing file `{tok}`")
            if is_hist or re.match(r"^\s*(- \*\*|\d+\.\s+\*\*)\s*\d{4}-\d{2}-\d{2}", line) or "DONE" in line or "~~" in line:
                pass
            else:
                for m in re.finditer(rf"\b(after|about|due|review-by|waiting until|on or after)\s+({DATE})\b", line):
                    try:
                        d = datetime.date.fromisoformat(m.group(2))
                    except ValueError:
                        continue
                    if d < today:
                        add("WARN", rel, i, f"follow-up date {m.group(2)} has passed ({m.group(1)}); re-check or mark DONE")
            hm = re.match(r"^(#{1,6})\s+(\d+(?:\.\d+)*)[.)]?\s", line)
            if hm:
                key = (len(hm.group(1)), hm.group(2))
                if key in seen_heads:
                    add("WARN", rel, i, f"duplicate heading number {hm.group(2)} (first at line {seen_heads[key]})")
                else:
                    seen_heads[key] = i
        # freshness header (method 10.1)
        fm, fm_end = _myco.frontmatter(text)
        if fm:
            dates = {}
            for k in ("last_confirmed", "review_after", "valid_from", "valid_until"):
                if k in fm and fm[k][0]:
                    v, ln = fm[k]
                    try:
                        if not re.fullmatch(DATE, v):
                            raise ValueError(v)
                        dates[k] = datetime.date.fromisoformat(v)
                    except ValueError:
                        add("WARN", rel, ln, f"header {k} '{v}' is not a date (use YYYY-MM-DD)")
            lc, ra, vf, vu = (dates.get(k) for k in ("last_confirmed", "review_after", "valid_from", "valid_until"))
            if lc and lc > today:
                add("WARN", rel, fm["last_confirmed"][1], f"last_confirmed {lc} is in the future")
            if ra and ra < today and not is_hist:
                add("WARN", rel, fm["review_after"][1], f"review date {ra} has passed: verify the content, then move last_confirmed and review_after")
            elif lc and not ra and not is_hist and (today - lc).days > stale_days:
                add("WARN", rel, fm["last_confirmed"][1], f"last confirmed {lc}, {(today - lc).days} days ago (limit {stale_days:.0f}): verify it or set review_after")
            if vu and vu < today and not is_hist:
                add("WARN", rel, fm["valid_until"][1], f"valid_until {vu} has passed: supersede the note or move it to the archive")
            if vf and vu and vf > vu:
                add("WARN", rel, fm["valid_from"][1], f"valid_from {vf} is after valid_until {vu}")
            for k in ("supersedes", "superseded_by"):
                if k not in fm:
                    continue
                for tok in [t.strip().strip("`") for t in fm[k][0].split(",") if t.strip()]:
                    if tok in paths or join_rel(rel, tok) in paths or tok in names:
                        for other in md_files:
                            if other != rel and (other == tok or os.path.basename(other) == os.path.basename(tok)):
                                mentions[other] += 1
                    else:
                        add("WARN", rel, fm[k][1], f"header {k} points to a missing note `{tok}`")
                if k == "superseded_by" and fm[k][0] and not is_hist:
                    add("INFO", rel, fm[k][1], f"superseded by {fm[k][0]}: move it to the archive when it is no longer needed")
        # fresh
        head = "\n".join(lines[fm_end:fm_end + 12])
        hm = re.search(rf"Last updated\s+({DATE})", head)
        if hm and not is_hist:
            cl = re.search(r"^#{1,3}\s+.*changelog.*$", text, re.M | re.I)
            entries = [e for e in re.findall(rf"^- \*\*({DATE})", text[cl.end():] if cl else "", re.M) if e <= (today + datetime.timedelta(days=1)).isoformat()]
            if entries and max(entries) > hm.group(1):
                add("WARN", rel, 1, f"header says last updated {hm.group(1)} but the changelog has entries up to {max(entries)}")

    # ledger completeness
    for rel in md_files:
        if os.path.basename(rel) != "sync-tracker.md":
            continue
        in_table, header = False, []
        for i, line in enumerate(texts[rel].split("\n"), 1):
            if re.match(r"^##\s+Sync targets", line):
                in_table, header = True, []
                continue
            if in_table and line.startswith("## "):
                in_table = False
            if in_table and line.startswith("|"):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if not header:
                    header = [c.lower() for c in cells]
                    continue
                if set("".join(cells)) <= set("-: "):
                    continue
                if len(cells) >= 4:
                    last, how = cells[2], cells[3]
                    if not last or last.startswith("_("):
                        add("WARN", rel, i, f"ledger row '{cells[0]}' has no last-synced date")
                    if not how or how.startswith("_("):
                        add("WARN", rel, i, f"ledger row '{cells[0]}' has no refresh method")
                    if len(cells) >= 5 and re.search(r"pending|behind|stale", cells[4], re.I):
                        add("INFO", rel, i, f"ledger row '{cells[0]}' status: {cells[4][:60]}")

    # orphans (info)
    entry = {"AGENTS.md", "CLAUDE.md", "README.md"}
    for rel in md_files:
        if mentions[rel] == 0 and os.path.basename(rel) not in entry and not rel.startswith((".github/", docs_prefix, "template/") + history):
            add("INFO", rel, 1, "no other note mentions this file by name")

    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    findings.sort(key=lambda x: (order[x[0]], x[1], x[2]))
    n = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for lvl, f, ln, text in findings:
        n[lvl] += 1
        if lvl == "INFO" and a.quiet:
            continue
        print(f"{lvl:5} {f}:{ln} {text}")
    print(f"notes-lint: {len(md_files)} notes, {n['ERROR']} errors, {n['WARN']} warnings, {n['INFO']} info")
    if n["ERROR"]:
        return 2
    return 1 if (n["WARN"] or (a.strict and n["INFO"])) else 0


def run():
    try:
        return main()
    except secret_scan.ScanError as e:
        print(f"notes-lint: could not run: {e}")
        return 3
    except SystemExit:
        raise
    except Exception as e:  # a crash must never read as "warnings only"
        import traceback
        traceback.print_exc()
        print(f"notes-lint: could not run: {type(e).__name__}: {e}")
        return 3


if __name__ == "__main__":
    sys.exit(run())
