#!/usr/bin/env python3
"""notes-commit: commit everything changed in the notes repository with a described message.

  tools/notes-commit "what changed and why"            commit only
  tools/notes-commit -p "message"                      also mirror afterwards (tools/mirror-push)
  tools/notes-commit --human "message"                 no AI co-author trailer
  tools/notes-commit --tag audit-2026-11-03 "message"  also create a milestone tag
  tools/notes-commit --branch my-change "message"      commit on that branch (created from the current one if new)
  tools/notes-commit --push "message"                  also git push the current branch to origin (sets the upstream)
  tools/notes-commit --pr "message"                    --push, then open a pull request with the GitHub CLI (gh pr create --fill)
  tools/notes-commit --folder NAME=MODE "message"      decide what to do with a new root folder or file (MODE: add, ignorelint, ignoreall, later)
  tools/notes-commit --only PATH [--only PATH] "msg"   commit only these files or folders; other changes stay uncommitted
  tools/notes-commit --dry-run "message"               show what would be committed (and any new-folder decision); change nothing

Behaviour (configured in .myco-notes.conf at the repository root):
  - stages everything (respecting .gitignore), or only the --only paths; says "nothing to commit" and exits 0 when clean
  - new folders and loose files in the repository root are not staged silently: NEW_FOLDERS in the conf says what to do
    (ask = the default: commit nothing, print a report and exit 3 so the agent can ask the owner; add = track and treat as notes;
    ignorelint = track but skip in the lint and the map; ignoreall = do not track, do not lint). Decisions are written to the conf and .gitignore
  - syntax-checks changed shell and python files before committing (CHECK_SH / CHECK_PY)
  - the pre-commit hook (<tools folder>/hooks/pre-commit) refuses commits that add secret-looking lines
  - adds AGENT_TRAILER unless --human
  - after the commit, if a changed file matches SYNC_GLOBS, runs SYNC_CMD so the live copy equals the committed copy
  - team use: --branch / --push / --pr give each change its own branch and pull request (see docs/TEAM.md)
  - with --only, the syntax checks and SYNC_GLOBS look only at the files this commit contains; other staged files stay staged
Exit: 0 committed (or nothing to commit), 1 a check failed, 2 usage, 3 new root folders need a decision (nothing committed),
      4 committed, but -p's mirror push did not sync (it failed, or was skipped with MIRROR_STRICT=1)
"""
import argparse
import collections
import fnmatch
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import _myco  # noqa: E402


def fail(msg, code=1):
    print(f"notes-commit: {msg}", file=sys.stderr)
    sys.exit(code)


def shell_syntax_ok(path):
    """bash -n on Linux and macOS; skipped on Windows, where `bash` may be an unrelated launcher."""
    if _myco.IS_WINDOWS or not shutil.which("bash"):
        return True
    return subprocess.run(["bash", "-n", path]).returncode == 0


def is_shell_script(path, hooks_prefix="tools/hooks/"):
    if path.endswith(".sh") or path.startswith(hooks_prefix):
        return True
    try:
        with open(path, "rb") as fh:
            first = fh.readline(200)
    except OSError:
        return False
    return first.startswith(b"#!") and (b"sh" in first)


MODES = ("add", "ignorelint", "ignoreall")
CHOICES = MODES + ("later",)
EXPLAIN = {
    "add": "track it and treat it as notes (the lint and the map read it)",
    "ignorelint": "track it (versioned), but the lint and the map skip it",
    "ignoreall": "do not track it and do not lint it (data, ignored by git)",
    "later": "leave it out of this commit; you are asked again next time",
}
OK_FILES = {"AGENTS.md", "CLAUDE.md", "README.md", ".gitignore", ".gitattributes", "CODEOWNERS", ".editorconfig"}
OK_PREFIXES = (".myco-notes", "LICENSE", "NOTICE")
OK_DIRS = {".github", ".agents", ".codex", ".claude", ".githooks"}
SECRETISH = re.compile(r"(?i)(credential|secret|passw|token|id_rsa|id_ed25519|\.pfx$|\.p12$|\.ovpn$)")
HINT = re.compile(r"(?i)(input|output|download|export|import|attach|upload|tmp|temp|scratch|dump|backup|cache|files?[-_])")
BULKY = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".7z", ".gz", ".tar", ".rar", ".png", ".jpg", ".jpeg", ".gif",
         ".mp4", ".mov", ".csv", ".log", ".db", ".sqlite", ".bin", ".exe", ".msi", ".iso", ".eml", ".msg", ".pcap", ".har"}


def new_items(lay):
    """New folders and loose files in the repository root that git does not track yet and nothing in the configuration covers.
    Returns {name: {"kind": "folder"|"file", "files": [paths]}}. Called from the repository root."""
    exempt = OK_DIRS | {lay[k].split("/")[0] for k in ("notes", "tools", "docs", "map") if lay[k]} | {d for d in lay["data"] if "/" not in d}
    tracked = {p.split("/")[0] for p in _myco.git("-c", "core.quotepath=off", "ls-files", "-z").stdout.split("\0") if p}
    out = {}
    for p in _myco.git("-c", "core.quotepath=off", "ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0"):
        if not p:
            continue
        top = p.split("/")[0]
        if "/" in p:
            if top in exempt or top in tracked:
                continue
            out.setdefault(top, {"kind": "folder", "files": []})["files"].append(p)
        elif p not in OK_FILES and not p.startswith(OK_PREFIXES):
            out[p] = {"kind": "file", "files": [p]}
    return out


def describe(info):
    files = info["files"]
    size = 0
    for f in files:
        try:
            size += os.path.getsize(f)
        except OSError:
            pass
    exts = collections.Counter((os.path.splitext(f)[1].lower() or "(none)") for f in files)
    md = sum(1 for f in files if f.lower().endswith(".md"))
    bulky = sum(1 for f in files if os.path.splitext(f)[1].lower() in BULKY)
    n = len(files)
    name = files[0].split("/")[0]
    if any(SECRETISH.search(os.path.basename(f)) for f in files):
        pick, why = "ignoreall", "some file names look like credentials or keys"
    elif md and md * 2 >= n and size < 2 * 1024 * 1024:
        pick, why = "add", "mostly Markdown"
    elif size > 5 * 1024 * 1024:
        pick, why = "ignoreall", "large"
    elif bulky * 2 >= n:
        pick, why = "ignoreall", "mostly documents, archives or data exports"
    elif HINT.search(name):
        pick, why = "ignoreall", "the name suggests inputs, outputs or downloads"
    else:
        pick, why = "ignorelint", "small mixed files: keep their history without checking them as notes"
    return {"count": n, "size": size, "exts": exts.most_common(4), "md": md, "pick": pick, "why": why}


def report_new(items, pending):
    print("notes-commit: NEW at the repository root, and nothing was committed. Ask the owner what to do with each:\n")
    for name in pending:
        d = describe(items[name])
        mix = ", ".join(f"{e} {c}" for e, c in d["exts"])
        label = name + ("/" if items[name]["kind"] == "folder" else "")
        what = "file" if items[name]["kind"] == "file" else f"folder: {d['count']} file{'s' if d['count'] != 1 else ''}"
        print(f"  {label}  ({what}, {_myco.human_size(d['size'])}; {mix})")
        print(f"      suggestion: {d['pick']} ({d['why']})")
        for f in items[name]["files"][:4]:
            print(f"      - {f}")
        if len(items[name]["files"]) > 4:
            print(f"      - ... and {len(items[name]['files']) - 4} more")
    print("\nChoices:")
    for m in CHOICES:
        print(f"  {m:<11} {EXPLAIN[m]}")
    args = " ".join(f"--folder {n}={describe(items[n])['pick']}" for n in pending)
    print(f"\nThen run again with the answers, for example:\n  notes-commit {args} \"message\"")
    print("(NAME=* answers for every new item at once.) To stop being asked, set NEW_FOLDERS=add, ignorelint or ignoreall in .myco-notes.conf.")
    print("Exit code 3 means: waiting for the owner's decision. An agent must ask the owner and must not choose.")


def apply_choice(root, lay, name, kind, mode):
    """Record the decision in .myco-notes.conf, .gitignore and .myco-notes-lint-ignore (the caller stages everything afterwards)."""
    if mode in ("add", "later"):
        return
    def append(path, line):
        full = os.path.join(root, path)
        old = open(full, encoding="utf-8").read() if os.path.exists(full) else ""
        if line in old.splitlines():
            return
        with open(full, "a", encoding="utf-8", newline="") as fh:
            fh.write(("" if old.endswith("\n") or not old else "\n") + line + "\n")
    if kind == "folder":
        cf = os.path.join(root, ".myco-notes.conf")
        text = open(cf, encoding="utf-8").read()
        if name not in lay["data"]:
            with open(cf, "w", encoding="utf-8", newline="") as fh:
                fh.write(_myco.set_conf_key(text, "DATA_DIRS", " ".join(lay["data"] + [name])))
            lay["data"].append(name)
        if mode == "ignoreall":
            append(".gitignore", f"/{name}/")
    elif mode == "ignoreall":
        append(".gitignore", f"/{name}")
    elif name.lower().endswith(".md"):   # ignorelint on a loose Markdown file: the lint reads it as a note unless told not to
        append(".myco-notes-lint-ignore", "^" + re.escape(name) + ":")


def preview(root, only, later):
    """Stage into a throw-away copy of the index and list what the commit would contain; the real index is untouched."""
    import tempfile
    idx = _myco.git("rev-parse", "--git-path", "index").stdout.strip()
    fd, tmp = tempfile.mkstemp(prefix="myco-index-")
    os.close(fd)
    try:
        if os.path.isfile(os.path.join(root, idx)):
            shutil.copyfile(os.path.join(root, idx), tmp)
        else:
            os.remove(tmp)
        env = dict(os.environ, GIT_INDEX_FILE=tmp)
        run = lambda *args: subprocess.run(["git", *args], cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        run("add", "-A", "--", *(only or ["."]))
        for n in later:
            run("rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", n)
        lines = run("-c", "core.quotepath=off", "diff", "--cached", "--name-status", "--", *(only or ["."])).stdout.splitlines()
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if not lines:
        print("notes-commit: dry run, nothing to commit")
        return 0
    print(f"notes-commit: dry run, would commit {len(lines)} file(s):")
    for l in lines:
        print("  " + l.replace("\t", "  "))
    return 0


def main():
    _myco.utf8_console()
    ap = argparse.ArgumentParser(prog="notes-commit", add_help=False)
    ap.add_argument("-p", action="store_true")
    ap.add_argument("--human", action="store_true")
    ap.add_argument("--tag", default="")
    ap.add_argument("--branch", default="")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--pr", action="store_true")
    ap.add_argument("--folder", action="append", default=[])
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    ap.add_argument("message", nargs="*")
    a = ap.parse_args()
    if a.help:
        print(__doc__)
        return 0
    msg = " ".join(a.message).strip()
    if not msg:
        print('usage: notes-commit [-p] [--human] [--tag NAME] [--branch NAME] [--push|--pr] [--folder NAME=MODE] [--only PATH] [--dry-run] "message"', file=sys.stderr)
        return 2

    root = _myco.toplevel(os.environ.get("NOTES_ROOT") or os.getcwd()) or _myco.toplevel(HERE)
    if not root:
        fail("not inside a git repository", 2)
    os.chdir(root)
    conf = _myco.load_conf(root)
    check_sh, check_py = conf.get("CHECK_SH", "1") == "1", conf.get("CHECK_PY", "1") == "1"
    trailer, sync_globs, sync_cmd = conf.get("AGENT_TRAILER", ""), conf.get("SYNC_GLOBS", "").split(), conf.get("SYNC_CMD", "")
    hooks_prefix = _myco.layout(root, conf)["tools"] + "/hooks/"

    only = [(_myco._rel(p) or p.strip()) for p in a.only]
    for p in only:
        if not os.path.exists(p) and _myco.git("ls-files", "--error-unmatch", "--", p).returncode != 0:
            fail(f"--only {p}: no such file or folder, and git does not know it", 2)
    if a.branch and not a.dry_run:
        cur = _myco.git("symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
        if cur != a.branch:
            exists = _myco.git("show-ref", "-q", "--verify", f"refs/heads/{a.branch}").returncode == 0
            r = _myco.git("switch", "-q", *([] if exists else ["-c"]), a.branch)
            if r.returncode:
                fail(f"cannot switch to branch {a.branch}: {r.stderr.strip()}")
            print(f"notes-commit: on branch {a.branch}")

    later = []
    lay = _myco.layout(root, conf)
    mode = (conf.get("NEW_FOLDERS") or "ask").strip().lower()
    if mode not in ("ask",) + MODES:
        fail(f"NEW_FOLDERS={mode!r} in .myco-notes.conf is not one of ask, add, ignorelint, ignoreall", 2)
    given = {}
    for spec in a.folder:
        name, _, choice = spec.partition("=")
        name, choice = (_myco._rel(name) or name.strip()), choice.strip().lower()
        if choice not in CHOICES:
            fail(f"--folder {spec}: the choice must be one of {', '.join(CHOICES)}", 2)
        given[name] = choice
    items = new_items(lay) if os.path.isfile(os.path.join(root, ".myco-notes.conf")) else {}
    if only:   # only the new items this commit would touch need a decision
        items = {n: v for n, v in items.items() if any(p == n or p.startswith(n + "/") or n.startswith(p.rstrip("/") + "/") for p in only)}
    stray = [n for n in given if n != "*" and n not in items]
    if stray:
        fail(f"--folder {stray[0]}: no new folder or file with that name at the repository root (new now: {', '.join(items) or 'none'})", 2)
    if items:
        chosen = {n: given.get(n) or given.get("*") or (mode if mode != "ask" else None) for n in items}
        pending = [n for n, c in chosen.items() if c is None]
        if pending:
            report_new(items, pending)
            return 3
        for n, c in chosen.items():
            if a.dry_run:
                print(f"notes-commit: would treat new {items[n]['kind']} {n} as: {c}")
                continue
            apply_choice(root, lay, n, items[n]["kind"], c)
            print(f"notes-commit: new {items[n]['kind']} {n}: {c}")
        later = [n for n, c in chosen.items() if c == "later"]

    if a.dry_run:
        return preview(root, only, later)
    _myco.git("add", "-A", "--", *(only or ["."]))
    for n in later:
        _myco.git("rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", n)
    if later:
        print("notes-commit: left out of this commit (asked again next time): " + ", ".join(later))
    if _myco.git("diff", "--cached", "--quiet").returncode == 0:
        print("notes-commit: nothing to commit")
        return 0

    changed = [f for f in _myco.git("-c", "core.quotepath=off", "diff", "--cached", "--name-only", "--diff-filter=AM", "-z",
                                    *(["--"] + only if only else [])).stdout.split("\0") if f]
    for f in changed:
        if f.endswith(".py") and check_py:
            try:
                with open(f, "rb") as fh:
                    compile(fh.read(), f, "exec")
            except (SyntaxError, ValueError) as e:
                fail(f"syntax error in {f} ({e}), not committing")
        elif check_sh and os.path.isfile(f) and is_shell_script(f, hooks_prefix):
            if not shell_syntax_ok(f):
                fail(f"syntax error in {f}, not committing")

    full = msg + (f"\n\n{trailer}" if trailer and not a.human else "")
    rc = subprocess.run(["git", "commit", "-q", "-m", full, *(["--"] + only if only else [])]).returncode  # hook output goes to the terminal
    if rc:
        return rc
    print(_myco.git("log", "--oneline", "-1").stdout.strip())
    print("\n".join(_myco.git("show", "--stat", "--format=", "HEAD").stdout.rstrip("\n").splitlines()[-6:]))
    if a.tag:
        if _myco.git("tag", a.tag).returncode == 0:
            print(f"tagged {a.tag}")

    if sync_globs and sync_cmd and any(fnmatch.fnmatchcase(f, g) for g in sync_globs for f in changed):
        r = _myco.run_shell(sync_cmd, root)
        if r.returncode:
            print("WARNING: committed, but SYNC_CMD failed; the live copy may differ from the committed copy", file=sys.stderr)

    result = 0
    if a.p:
        mrc = subprocess.run([sys.executable, os.path.join(HERE, "mirror_push.py")]).returncode
        if mrc:
            print(f"WARNING: committed, but the mirror push did not sync (mirror-push exit {mrc})", file=sys.stderr)
            result = 4

    if a.push or a.pr:
        cur = _myco.git("symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
        if not cur:
            print("WARNING: detached HEAD, not pushing", file=sys.stderr)
            return result
        if _myco.git("remote", "get-url", "origin").returncode != 0:
            print("WARNING: no remote named origin; nothing pushed", file=sys.stderr)
            return result
        if subprocess.run(["git", "push", "-q", "-u", "origin", cur]).returncode == 0:
            print(f"pushed {cur} to origin")
        else:
            print("WARNING: committed, but git push failed", file=sys.stderr)
        if a.pr:
            if shutil.which("gh"):
                if subprocess.run(["gh", "pr", "create", "--fill"]).returncode:
                    print("WARNING: pull request not created (does one exist already?)", file=sys.stderr)
            else:
                print(f"gh (GitHub CLI) not installed: open a pull request for branch {cur} in your hosting service")
    return result


if __name__ == "__main__":
    sys.exit(main())
