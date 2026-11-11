#!/usr/bin/env python3
"""relocate: move an existing notes tree into the single-folder layout (or rename that folder) and fix the paths that name it.

  python3 <kit>/tools/relocate.py --root PROJECT [--myco-dir myco-notes] [--apply] [--no-refresh]
  tools/relocate [--myco-dir myco-notes] [--apply]            (from the project's own tools folder, on Linux and macOS)

Updating a tree that already uses the single folder: run the same command from a newer kit. There is nothing to move, so it only refreshes the
project's tools and docs from that kit (dry run lists the new and changed files; --apply copies them; never bootstrap, never your notes, conf or
AGENTS.md) and tells you what else is worth checking (NEW_FOLDERS in the conf, the AGENTS.md rule about exit code 3).

Before 0.6.0 a notes tree had notes/, tools/, docs/ and notes-map/ at the repository root. The single-folder layout puts the notes
themselves, tools/, docs/ and map/ inside one folder (default myco-notes/), so the root stays free for the project's own folders.
The same command renames the folder when the tree already uses it (--myco-dir new-name).

Dry run by default: it prints the moves and the text changes and touches nothing. With --apply it
  1. moves the folders (git mv for tracked files), keeping the names your tree uses (a docs folder called myco-docs stays myco-docs)
  2. rewrites path references in .md, .yml, .yaml and .json files, the conf, .gitignore and CODEOWNERS, but only where the text names a
     path that really existed (so prose such as "release notes/changes" is left alone)
  3. updates .myco-notes.conf (MYCO_DIR, NOTES_DIR, TOOLS_DIR, DOCS_DIR, MAP_DIR), .gitignore and core.hooksPath
  4. when run from a kit folder (not from the project's own tools), refreshes the project's tools and docs from that kit
     (--no-refresh to skip), because older tools do not understand the new layout
It does not commit: review `git status` and `git diff`, run the lint, then commit with the notes-commit tool. The old generated map
folder is left in place; delete it yourself when you are happy. Undo: git restore --staged --worktree . (before committing), or revert.
On Windows run it from the kit folder, not from the project's own tools folder (Windows cannot move a folder it is running from).
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import _myco  # noqa: E402

TEXT_EXT = (".md", ".yml", ".yaml", ".json")
TEXT_NAMES = {".gitignore", "CODEOWNERS", ".myco-notes.conf", ".myco-notes-lint-ignore"}
SKIP_NAMES = {".git", "__pycache__", "node_modules", "site"}
NOT_COPIED = {"bootstrap", "bootstrap.cmd", "bootstrap.py", "__pycache__"}
MAX_TEXT = 5_000_000


def fail(msg, code=1):
    print(f"relocate: {msg}", file=sys.stderr)
    sys.exit(code)


def under(rel, d):
    return rel == d or rel.startswith(d + "/")


def absp(root, rel):
    return os.path.join(root, *rel.split("/"))


def list_tree(root, rel_dirs):
    """Every file and folder (relative, forward slashes) in and below the given folders."""
    out = set()
    for d in rel_dirs:
        base = absp(root, d)
        if not os.path.isdir(base):
            continue
        out.add(d)
        for dp, dn, fn in os.walk(base):
            dn[:] = [x for x in dn if x not in {"__pycache__", ".git"}]
            for n in dn + fn:
                out.add(os.path.relpath(os.path.join(dp, n), root).replace(os.sep, "/"))
    return out


def lint_summary(root, tools_rel):
    lint = os.path.join(absp(root, tools_rel), "notes-lint.py")
    if not os.path.isfile(lint):
        return "lint not available"
    r = subprocess.run([sys.executable, "-B", lint, "--root", root, "--quiet"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = [l for l in (r.stdout or "").splitlines() if l.startswith("notes-lint:")]
    return lines[-1] if lines else "lint printed no summary"


def main():
    _myco.utf8_console()
    ap = argparse.ArgumentParser(prog="relocate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="the notes project (default: the repository this tools folder belongs to)")
    ap.add_argument("--myco-dir", default="myco-notes")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-refresh", action="store_true")
    a = ap.parse_args()

    root = _myco.toplevel(os.path.abspath(a.root) if a.root else HERE)
    if not root or not os.path.isfile(os.path.join(root, ".myco-notes.conf")):
        fail("no .myco-notes.conf at the repository root; run this on a Myco Notes project (pass --root PROJECT)")
    my = _myco._rel(a.myco_dir)
    if not my or "/" in my or my.startswith("."):
        fail("--myco-dir must be one plain folder name, for example myco-notes", 2)

    conf = _myco.load_conf(root)
    old = _myco.layout(root, conf)
    self_run = under(os.path.realpath(HERE).replace(os.sep, "/"), os.path.realpath(absp(root, old["tools"])).replace(os.sep, "/"))
    if self_run and _myco.IS_WINDOWS and a.apply:
        fail("Windows cannot move the folder this command runs from. Run it from the kit folder instead:\n"
             f"  python <kit>\\tools\\relocate.py --root \"{root}\" --apply")
    if a.apply and _myco.git("-C", root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        fail("the working tree has uncommitted changes; commit or stash them first so the move is one reviewable change")

    umbrella = bool(old["myco"]) and all(under(old[k], old["myco"]) for k in ("notes", "tools", "docs", "map"))
    if umbrella:
        if old["myco"] == my:
            return refresh_only(root, old, conf, a, self_run)
        new = {k: my + old[k][len(old["myco"]):] for k in ("notes", "tools", "docs", "map")}
        moves = [(old["myco"], my)]
        pairs = [(old["myco"] + "/", my + "/")]
        oldset = list_tree(root, [old["myco"]])
    else:
        new = {"notes": my, "tools": my + "/" + old["tools"].rsplit("/", 1)[-1], "docs": my + "/" + old["docs"].rsplit("/", 1)[-1], "map": my + "/map"}
        moves = []
        if old["notes"] != my:
            if os.path.isdir(absp(root, my)) and os.listdir(absp(root, my)):
                fail(f"{my}/ already exists and is not empty; choose another --myco-dir or empty it first")
            for child in sorted(os.listdir(absp(root, old["notes"]))) if os.path.isdir(absp(root, old["notes"])) else []:
                moves.append((old["notes"] + "/" + child, my + "/" + child))
        for k in ("tools", "docs"):
            if os.path.isdir(absp(root, old[k])) and old[k] != new[k] and not under(old[k], old["notes"]):
                moves.append((old[k], new[k]))
        pairs = [(old[k] + "/", new[k] + "/") for k in ("notes", "tools", "docs", "map") if old[k] != new[k]]
        oldset = list_tree(root, [old["notes"], old["tools"], old["docs"]])
        dsts = [d for _s, d in moves]
        srcs = {s for s, _d in moves}
        if len(set(dsts)) != len(dsts):
            fail("two folders would land on the same name inside " + my + "/ (for example a notes subfolder called tools or docs); rename one first")
        for _s, d in moves:
            if os.path.exists(absp(root, d)) and d not in srcs:
                fail(f"{d} already exists; move it away first")

    old_map_prefix = old["map"] + "/"
    newp = dict(pairs)
    alts = "|".join(re.escape(p) for p, _ in sorted(pairs, key=lambda x: -len(x[0])))
    pat = re.compile(r"(?<![\w./~-])(" + alts + r")([^\s`'\")\]|,;<>]*)") if alts else None
    counter = {"n": 0}

    def repl(m):
        pfx, rest = m.group(1), m.group(2)
        core = rest.rstrip(".:!?")
        s = m.string
        prev = s[m.start() - 1] if m.start() else "\n"
        nxt = s[m.end()] if m.end() < len(s) else "\n"
        if core == "":
            ok = prev in "`(\"'=\n" and nxt in "`)\"'\n ,.;:"
        elif core.startswith("*"):
            ok = True
        elif pfx == old_map_prefix and not umbrella:
            ok = core.split("/")[0] in ("index.html", "p", "raw")
        else:
            ok = (pfx + core).rstrip("/") in oldset
        if not ok:
            return m.group(0)
        counter["n"] += 1
        return newp[pfx] + rest

    skip_dirs = [old["tools"], old["map"], *lay_data(conf)]
    rewritten, per_file = {}, {}
    for dp, dn, fn in os.walk(root):
        reldir = os.path.relpath(dp, root).replace(os.sep, "/")
        dn[:] = sorted(d for d in dn if d not in SKIP_NAMES and not any(under(d if reldir == "." else reldir + "/" + d, s) for s in skip_dirs))
        for f in sorted(fn):
            if not (f.endswith(TEXT_EXT) or f in TEXT_NAMES):
                continue
            p = os.path.join(dp, f)
            rel = f if reldir == "." else reldir + "/" + f
            try:
                if os.path.getsize(p) > MAX_TEXT:
                    continue
                with open(p, encoding="utf-8", newline="") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            counter["n"] = 0
            out = pat.sub(repl, text) if pat else text
            if out != text:
                rewritten[rel] = out
                per_file[rel] = counter["n"]

    print(f"relocate: {'APPLYING' if a.apply else 'dry run (nothing is changed; add --apply to do it)'}")
    print(f"  project: {root}")
    print("  layout now : " + ", ".join(f"{k}={old[k]}" for k in ("notes", "tools", "docs", "map")))
    print("  layout new : " + ", ".join(f"{k}={new[k]}" for k in ("notes", "tools", "docs", "map")))
    print(f"  moves ({len(moves)}):")
    for s, d in moves[:40]:
        print(f"    {s} -> {d}")
    if len(moves) > 40:
        print(f"    ... and {len(moves) - 40} more")
    print(f"  path references to rewrite: {sum(per_file.values())} in {len(per_file)} files" + ("" if not per_file else " (most: " + ", ".join(f"{k} {v}" for k, v in sorted(per_file.items(), key=lambda kv: -kv[1])[:4]) + ")"))
    refresh = not a.no_refresh and not self_run
    print("  tools and docs refreshed from this kit: " + ("yes" if refresh else "no (" + ("run from the project's own tools folder" if self_run else "--no-refresh") + ")"))
    print(f"  lint before: {lint_summary(root, old['tools'])}")
    if not a.apply:
        return 0

    def newpath(rel):
        for s, d in moves:
            if under(rel, s):
                return d + rel[len(s):]
        return rel

    if not umbrella:
        os.makedirs(absp(root, my), exist_ok=True)       # (a renamed folder must not exist yet, or git mv would move into it)
    for s, d in moves:
        src, dst = absp(root, s), absp(root, d)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if _myco.git("-C", root, "mv", s, d).returncode != 0:        # untracked or ignored files: plain move
            shutil.move(src, dst)
    if not umbrella and old["notes"] != my and os.path.isdir(absp(root, old["notes"])) and not os.listdir(absp(root, old["notes"])):
        os.rmdir(absp(root, old["notes"]))
    for rel, text in rewritten.items():
        with open(absp(root, newpath(rel)), "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    cp = os.path.join(root, ".myco-notes.conf")
    with open(cp, encoding="utf-8", newline="") as fh:
        ctext = fh.read()
    for key, val in (("MYCO_DIR", my), ("NOTES_DIR", new["notes"]), ("TOOLS_DIR", new["tools"]), ("DOCS_DIR", new["docs"]), ("MAP_DIR", new["map"])):
        ctext = _myco.set_conf_key(ctext, key, val)
    with open(cp, "w", encoding="utf-8", newline="") as fh:
        fh.write(ctext)
    _myco.record_kit_version(root)
    gi = os.path.join(root, ".gitignore")
    gtext = open(gi, encoding="utf-8", newline="").read() if os.path.isfile(gi) else ""
    if new["map"] + "/" not in [l.strip() for l in gtext.splitlines()]:
        with open(gi, "w", encoding="utf-8", newline="") as fh:
            fh.write(gtext + ("" if gtext.endswith("\n") or not gtext else "\n") + new["map"] + "/\n")

    if refresh:
        shutil.copytree(HERE, absp(root, new["tools"]), dirs_exist_ok=True, ignore=lambda d, names: [n for n in names if n in NOT_COPIED])
        kit_docs = os.path.join(KIT, "docs")
        if os.path.isdir(kit_docs):
            os.makedirs(absp(root, new["docs"]), exist_ok=True)
            for f in os.listdir(kit_docs):
                if f.endswith(".md"):
                    shutil.copy2(os.path.join(kit_docs, f), os.path.join(absp(root, new["docs"]), f))
    _myco.git("-C", root, "config", "core.hooksPath", new["tools"] + "/hooks")
    if not _myco.IS_WINDOWS:
        for d in (new["tools"], new["tools"] + "/hooks"):
            full = absp(root, d)
            if os.path.isdir(full):
                for f in os.listdir(full):
                    p = os.path.join(full, f)
                    if os.path.isfile(p):
                        os.chmod(p, os.stat(p).st_mode | 0o111)

    print(f"  lint after : {lint_summary(root, new['tools'])}")
    print(f"""
relocate: done, nothing committed. Next:
  1. git status and git diff --stat: review the moves and the {sum(per_file.values())} rewritten references.
  2. {new['tools']}/notes-lint.py : the warning count should not be higher than "lint before" above.
  3. python3 {new['tools']}/build-notes-map.py, then commit: {new['tools']}/notes-commit "Move the notes into {my}/ (Myco Notes 0.6 layout)"
  4. Delete the old generated map folder ({old['map']}/) if it is still there, and update anything outside this repository that names the old
     paths (mirror or sync commands, scheduled jobs, CI settings, other tools).
  Undo before committing: git restore --staged --worktree . and remove {my}/ if it is left over.""")
    return 0


def same_text(a, b):
    """Files are the same when their bytes match, ignoring a Windows checkout's CRLF line endings."""
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read().replace(b"\r\n", b"\n") == fb.read().replace(b"\r\n", b"\n")
    except OSError:
        return False


def kit_version():
    return _myco.VERSION


def refresh_only(root, old, conf, a, self_run):
    """The tree already has the layout: refresh its tools and docs from this kit (dry run unless --apply)."""
    my = old["myco"]
    if a.no_refresh or self_run:
        why = "--no-refresh" if a.no_refresh else "this is the project's own tools folder, so there is no newer kit to copy from; run relocate.py from a newer kit folder"
        print(f"relocate: the tree already uses {my}/ ; nothing to do ({why})")
        return 0
    plan = []                      # (kit file, project file, label)
    for src_dir, dst_rel, only_md in ((HERE, old["tools"], False), (os.path.join(KIT, "docs"), old["docs"], True)):
        if not os.path.isdir(src_dir):
            continue
        for dp, dn, fn in os.walk(src_dir):
            dn[:] = [d for d in dn if d not in NOT_COPIED]
            for f in sorted(fn):
                if f in NOT_COPIED or (only_md and not f.endswith(".md")) or (only_md and dp != src_dir):
                    continue
                rel = os.path.relpath(os.path.join(dp, f), src_dir).replace(os.sep, "/")
                plan.append((os.path.join(dp, f), absp(root, dst_rel + "/" + rel), dst_rel + "/" + rel))
    new_f = [p for p in plan if not os.path.exists(p[1])]
    changed = [p for p in plan if os.path.exists(p[1]) and not same_text(p[0], p[1])]
    same = len(plan) - len(new_f) - len(changed)
    print(f"relocate: {'APPLYING' if a.apply else 'dry run (nothing is changed; add --apply to do it)'}")
    print(f"  project: {root}")
    print(f"  the tree already uses {my}/ ; nothing to move. Refreshing tools and docs from this kit (version {kit_version()}).")
    print(f"  new files ({len(new_f)}):" + "".join(f"\n    + {p[2]}" for p in new_f[:40]))
    print(f"  changed files ({len(changed)}):" + "".join(f"\n    ~ {p[2]}" for p in changed[:60]))
    print(f"  unchanged: {same}. Not touched: bootstrap, your notes, AGENTS.md, .gitignore; .myco-notes.conf only gets KIT_VERSION.")
    print(f"  lint before: {lint_summary(root, old['tools'])}")
    if a.apply:
        for src, dst, _label in new_f + changed:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        _myco.git("-C", root, "config", "core.hooksPath", old["tools"] + "/hooks")
        _myco.record_kit_version(root)
        if not _myco.IS_WINDOWS:
            for d in (old["tools"], old["tools"] + "/hooks"):
                full = absp(root, d)
                if os.path.isdir(full):
                    for f in os.listdir(full):
                        p = os.path.join(full, f)
                        if os.path.isfile(p):
                            os.chmod(p, os.stat(p).st_mode | 0o111)
        print(f"  lint after : {lint_summary(root, old['tools'])}")
    if not new_f and not changed:
        print("\nrelocate: tools and docs are already the same as this kit; nothing to refresh.")
    tips = []
    if "NEW_FOLDERS" not in conf:
        tips.append("NEW_FOLDERS is not set in .myco-notes.conf, so notes-commit stops and asks about every new root folder or file (the default, ask). Ask the owner whether they want ask, add, ignorelint or ignoreall and add the line.")
    try:
        agents = open(os.path.join(root, "AGENTS.md"), encoding="utf-8").read()
    except OSError:
        agents = ""
    if agents and "exit code 3" not in agents:
        tips.append("AGENTS.md does not tell agents what to do when notes-commit exits with code 3 (new root folders). Add the bullet from the kit's template/AGENTS.md (the one starting 'If notes-commit stops with exit code 3').")
    print("\nrelocate: " + ("done, nothing committed. Next:" if a.apply else "this was a dry run. With --apply it copies the files above, then:"))
    print(f"  1. git status and git diff --stat; run {old['tools']}/notes-lint.py and python3 {old['tools']}/build-notes-map.py (it writes the graph view too).")
    print(f"  2. Commit: {old['tools']}/notes-commit \"Update the Myco Notes tools and docs to {kit_version()}\"")
    for i, t in enumerate(tips, 3):
        print(f"  {i}. {t}")
    if a.apply:
        print("  Undo before committing: git restore --staged --worktree . and delete any new untracked tool files listed above.")
    return 0


def lay_data(conf):
    return [d for d in (_myco._rel(x) for x in conf.get("DATA_DIRS", "").split()) if d]


if __name__ == "__main__":
    sys.exit(main())
