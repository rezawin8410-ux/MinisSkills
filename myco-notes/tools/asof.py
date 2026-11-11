#!/usr/bin/env python3
"""asof: what did the notes say on a given date, and what changed since (Python standard library only; read-only).

Git keeps every committed state of the notes (method 7.1). This tool picks the last commit at or before a date and shows the notes as they were.

  asof WHEN                      the commit that was current then, how many notes existed, and how much changed since
  asof WHEN NOTE                 the text of one note as it was (NOTE: a repository path, a path inside the notes folder, or a unique file name)
  asof WHEN --grep TEXT          lines containing TEXT (case-insensitive) in the notes as they were
  asof WHEN --since              the notes added, changed, renamed or deleted since then, and the commits in between
  asof --history NOTE            the commits that touched one note, newest first, following renames

WHEN is YYYY-MM-DD (the end of that day), a date with a time or an offset ("2026-09-01 12:00", "2026-09-01T23:59:59+02:00"), or any git
revision (a tag such as audit-2026-11-03, a commit hash, HEAD~5). A plain date uses the time zone of this machine, which is git's rule; give an
offset or a revision to be exact. Dates compare with the commit date.

Limits: only committed text is visible; a note may have had another name then (--history follows renames); the notes said it, which is not
always what was true. The note text goes to standard output; the header lines go to standard error, so the output can be piped.

Usage:  tools/asof [--root DIR] WHEN [NOTE] [--grep TEXT] [--since] | --history NOTE
Exit:   0 found, 1 nothing found for that date or note, 2 usage error or ambiguous name
"""
import argparse, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import _myco  # noqa: E402


def err(msg):
    print(msg, file=sys.stderr)


def resolve_rev(root, when):
    """(commit, text) for the last commit at or before `when`; commit is None when there is none."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
        when_git = when + " 23:59:59"
    elif re.match(r"\d{4}-\d{2}-\d{2}[ T]", when):
        when_git = when
    else:
        r = _myco.git("-C", root, "rev-parse", "--verify", "--quiet", when + "^{commit}")
        return (r.stdout.strip() or None), when
    r = _myco.git("-C", root, "rev-list", "-1", "--before=" + when_git, "HEAD")
    return (r.stdout.strip() or None), when_git


def describe(root, rev):
    r = _myco.git("-C", root, "log", "-1", "--format=%h %ad %s", "--date=iso", rev)
    return r.stdout.strip()


def note_files(root, rev, lay):
    """Markdown notes in the notes folder at rev, without the kit's own docs, tools and the generated map."""
    r = _myco.git("-C", root, "ls-tree", "-r", "--name-only", rev, "--", lay["notes"])
    skip = (lay["tools"] + "/", lay["docs"] + "/", lay["map"] + "/")
    return [f for f in r.stdout.split("\n") if f.endswith(".md") and not f.startswith(skip)]


def find_note(root, rev, lay, name):
    """Resolve NOTE to one repository path at rev. Returns (path, candidates)."""
    allf = [f for f in _myco.git("-C", root, "ls-tree", "-r", "--name-only", rev).stdout.split("\n") if f]
    name = name.replace("\\", "/").lstrip("./")
    for cand in (name, lay["notes"] + "/" + name):
        if cand in allf:
            return cand, []
    base = [f for f in allf if f.endswith(".md") and (f == name or f.endswith("/" + name) or os.path.basename(f) == os.path.basename(name))]
    if len(base) == 1:
        return base[0], []
    return None, base


def main():
    _myco.utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--grep", metavar="TEXT")
    ap.add_argument("--since", action="store_true")
    ap.add_argument("--history", metavar="NOTE")
    ap.add_argument("args", nargs="*", help="WHEN [NOTE]")
    a = ap.parse_args()
    root = _myco.toplevel(a.root)
    if not root:
        err("asof: not inside a git repository")
        return 2
    lay = _myco.layout(root)

    if a.history:
        path, cands = find_note(root, "HEAD", lay, a.history)
        if not path:
            # a note that is gone now: look for the name in the whole history
            r = _myco.git("-C", root, "log", "--all", "--format=", "--name-only", "--diff-filter=ACDMR")
            seen = sorted({f for f in r.stdout.split("\n") if f.endswith(".md") and (f == a.history or os.path.basename(f) == os.path.basename(a.history))})
            if len(seen) != 1:
                err(f"asof: no unique note named {a.history!r}" + ("; candidates: " + ", ".join(seen) if seen else ""))
                return 1 if not seen else 2
            path = seen[0]
        r = _myco.git("-C", root, "log", "--follow", "--format=%h %ad %s", "--date=short", "--", path)
        if not r.stdout.strip():
            err(f"asof: no commits touch {path}")
            return 1
        err(f"# history of {path}")
        sys.stdout.write(r.stdout)
        return 0

    if not a.args:
        ap.print_usage(sys.stderr)
        return 2
    when, rest = a.args[0], a.args[1:]
    rev, shown = resolve_rev(root, when)
    if not rev:
        roots = _myco.git("-C", root, "rev-list", "--max-parents=0", "HEAD").stdout.split()
        first = _myco.git("-C", root, "log", "-1", "--format=%ad", "--date=short", roots[-1]).stdout.strip() if roots else ""
        err(f"asof: nothing was committed at or before {when}" + (f" (the history starts {first})" if first else ""))
        return 1
    err(f"# notes as of {when}: {describe(root, rev)}")

    if rest:
        path, cands = find_note(root, rev, lay, rest[0])
        if not path:
            err(f"asof: {rest[0]!r} " + (f"is ambiguous: {', '.join(cands)}" if cands else "did not exist then (it may have had another name: asof --history NOTE)"))
            return 2 if cands else 1
        r = _myco.git("-C", root, "show", f"{rev}:{path}")
        if r.returncode != 0:
            err(f"asof: cannot read {path} at that commit")
            return 1
        sys.stdout.write(r.stdout)
        return 0

    if a.grep:
        r = _myco.git("-C", root, "grep", "-n", "-i", "-F", "-e", a.grep, rev, "--", lay["notes"])
        skip = (lay["tools"] + "/", lay["docs"] + "/", lay["map"] + "/")
        hits = []
        for line in r.stdout.split("\n"):
            m = re.match(r"^[^:]+:([^:]+):(\d+):(.*)$", line)
            if m and m.group(1).endswith(".md") and not m.group(1).startswith(skip):
                hits.append(f"{m.group(1)}:{m.group(2)}: {m.group(3).strip()}")
        sys.stdout.write("\n".join(hits) + ("\n" if hits else ""))
        if not hits:
            err(f"asof: no note contained {a.grep!r} at that date")
            return 1
        return 0

    if a.since:
        r = _myco.git("-C", root, "diff", "--name-status", "-M", rev, "HEAD", "--", lay["notes"])
        skip = (lay["tools"] + "/", lay["docs"] + "/", lay["map"] + "/")
        rows = [l for l in r.stdout.split("\n") if l.strip() and l.split("\t")[-1].endswith(".md") and not l.split("\t")[-1].startswith(skip)]
        n = _myco.git("-C", root, "rev-list", "--count", f"{rev}..HEAD").stdout.strip()
        print(f"{n} commit(s) since; {len(rows)} note(s) differ now")
        for l in rows:
            print(l.replace("\t", "  "))
        log = _myco.git("-C", root, "log", f"{rev}..HEAD", "--format=%h %ad %s", "--date=short")
        if log.stdout.strip():
            print("\ncommits since (newest first):")
            sys.stdout.write(log.stdout)
        return 0

    files = note_files(root, rev, lay)
    n = _myco.git("-C", root, "rev-list", "--count", f"{rev}..HEAD").stdout.strip()
    print(f"{len(files)} notes existed then; {n} commit(s) since (asof {when} --since lists them)")
    for f in files[:40]:
        print("  " + f)
    if len(files) > 40:
        print(f"  ... and {len(files) - 40} more (use NOTE, --grep or --since to look further)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:   # the reader (head, less) closed the pipe
        try:
            sys.stdout = None
        finally:
            os._exit(0)
