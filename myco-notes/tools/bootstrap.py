#!/usr/bin/env python3
"""bootstrap: create a new notes repository from the Myco Notes template.

  tools/bootstrap <target-dir> --name "Project name" [--owner "Name"] [--tz "MST (UTC-7)"] [--no-commit]
                  [--myco-dir myco-notes] [--layout nested|flat] [--data-dir files-input --data-dir files-output ...]
                  [--new-folders ask|add|ignorelint|ignoreall]

Layout (default nested): one folder, myco-notes/, holds the notes, tools/, docs/ and map/; only the files agents and git look for at the
root (AGENTS.md, CLAUDE.md, .github/, .gitignore, .gitattributes, .myco-notes.conf) stay outside it, so the root stays free for the
project's own folders. --layout flat gives the older arrangement (notes/, tools/, docs/ and notes-map/ at the root).
--data-dir (repeatable) names a project folder that is not notes (inputs, outputs, attachments): it is created, gitignored, listed in
the conf as DATA_DIRS and left out of the lint and the map.

--new-folders sets what notes-commit does with a new folder or loose file in the root (NEW_FOLDERS in the conf): ask (default; it stops and the
agent asks the owner), add (track it and lint it as notes), ignorelint (track it, skip the lint and the map), ignoreall (do not track, do not lint).
An agent setting up a tree for a person asks them which one they want first.

Copies template/ into <target-dir>, fills the {{PLACEHOLDERS}}, copies tools/ and docs/*.md, makes git use the versioned hooks
(core.hooksPath = <tools folder>/hooks), and makes the baseline commit. The target must be empty or not exist.
"""
import argparse
import datetime
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import _myco  # noqa: E402

NOT_COPIED = {"bootstrap", "bootstrap.cmd", "bootstrap.py", "__pycache__"}
TEXT_EXT = (".md", ".yml", ".yaml")
TEXT_NAMES = {".myco-notes.conf", ".gitignore", "CODEOWNERS"}


def main():
    _myco.utf8_console()
    ap = argparse.ArgumentParser(prog="bootstrap", add_help=True)
    ap.add_argument("target")
    ap.add_argument("--name", required=True)
    ap.add_argument("--owner", default=None)
    ap.add_argument("--tz", default="UTC")
    ap.add_argument("--no-commit", action="store_true")
    ap.add_argument("--layout", choices=("nested", "flat"), default="nested")
    ap.add_argument("--myco-dir", default="myco-notes")
    ap.add_argument("--data-dir", action="append", default=[])
    ap.add_argument("--new-folders", choices=("ask", "add", "ignorelint", "ignoreall"), default="ask")
    a = ap.parse_args()
    owner = a.owner or _myco.git("config", "user.name").stdout.strip() or "owner"

    my = _myco._rel(a.myco_dir) if a.layout == "nested" else ""
    if a.layout == "nested" and (not my or my.startswith(".")):
        print("bootstrap: --myco-dir must be a plain folder name", file=sys.stderr)
        return 2
    data = [d for d in (_myco._rel(x) for x in a.data_dir) if d]
    lay =_myco.layout("", {"MYCO_DIR": my} if my else {})   # same defaults every tool derives later

    def at(rel):
        return os.path.join(target, *rel.split("/"))

    target = os.path.abspath(a.target)
    if os.path.exists(target) and os.listdir(target):
        print(f"bootstrap: {target} exists and is not empty", file=sys.stderr)
        return 1
    os.makedirs(target, exist_ok=True)

    tpl = os.path.join(KIT, "template")
    for item in sorted(os.listdir(tpl)):
        src = os.path.join(tpl, item)
        if item == "notes":   # the template's notes folder lands in the notes folder of the chosen layout
            shutil.copytree(src, at(lay["notes"]), dirs_exist_ok=True)
        elif os.path.isdir(src):
            shutil.copytree(src, os.path.join(target, item), dirs_exist_ok=True)
        else:   # template/gitattributes becomes the tree's .gitattributes (in the kit it must not apply to the kit's own files)
            shutil.copy2(src, os.path.join(target, "." + item if item == "gitattributes" else item))
    shutil.copytree(HERE, at(lay["tools"]), dirs_exist_ok=True, ignore=lambda d, names: [n for n in names if n in NOT_COPIED])
    docs = os.path.join(KIT, "docs")
    os.makedirs(at(lay["docs"]), exist_ok=True)
    if os.path.isdir(docs):
        for f in os.listdir(docs):
            if f.endswith(".md"):
                shutil.copy2(os.path.join(docs, f), os.path.join(at(lay["docs"]), f))

    subs = (("{{PROJECT_NAME}}", a.name), ("{{OWNER}}", owner), ("{{TIMEZONE}}", a.tz), ("{{DATE}}", datetime.date.today().isoformat()),
            ("{{MYCO_DIR}}", lay["myco"]), ("{{NOTES_DIR}}", lay["notes"]), ("{{TOOLS_DIR}}", lay["tools"]), ("{{DOCS_DIR}}", lay["docs"]),
            ("{{MAP_DIR}}", lay["map"]), ("{{DATA_DIRS}}", " ".join(data)), ("{{NEW_FOLDERS}}", a.new_folders), ("{{KIT_VERSION}}", _myco.VERSION))
    tools_abs = os.path.normcase(at(lay["tools"]))
    for dp, dn, fn in os.walk(target):
        dn[:] = [d for d in dn if d != ".git" and os.path.normcase(os.path.join(dp, d)) != tools_abs]
        for f in fn:
            if f.endswith(TEXT_EXT) or f in TEXT_NAMES:
                p = os.path.join(dp, f)
                with open(p, encoding="utf-8", newline="") as fh:
                    s = fh.read()
                for k, v in subs:
                    s = s.replace(k, v)
                with open(p, "w", encoding="utf-8", newline="") as fh:
                    fh.write(s)

    if data:
        for d in data:
            os.makedirs(at(d), exist_ok=True)
        with open(os.path.join(target, ".gitignore"), "a", encoding="utf-8", newline="") as fh:
            fh.write("\n# Project data (inputs, outputs, attachments): not notes, not versioned. Delete a line to version that folder.\n")
            fh.write("".join(f"{d}/\n" for d in data))
        with open(os.path.join(target, "AGENTS.md"), "a", encoding="utf-8", newline="") as fh:
            fh.write("- Project data folders (inputs and outputs, not notes; ignored by git): " + ", ".join(f"`{d}/`" for d in data) + ".\n")

    if not _myco.IS_WINDOWS:
        for d in (lay["tools"], lay["tools"] + "/hooks"):
            full = at(d)
            for f in os.listdir(full):
                p = os.path.join(full, f)
                if os.path.isfile(p):
                    os.chmod(p, os.stat(p).st_mode | 0o111)

    if _myco.git("-C", target, "init", "-q", "-b", "main").returncode != 0:  # git older than 2.28
        _myco.git("-C", target, "init", "-q")
        _myco.git("-C", target, "symbolic-ref", "HEAD", "refs/heads/main")
    _myco.git("-C", target, "config", "core.hooksPath", lay["tools"] + "/hooks")
    print(f"bootstrap: created {target}")
    if not a.no_commit:
        _myco.git("-C", target, "add", "-A")
        r = subprocess.run(["git", "-C", target, "commit", "-q", "-m", "Baseline: notes tree created from the Myco Notes template"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            print(f"bootstrap: baseline commit failed ({r.stderr.strip()}); set git user.name and user.email, then commit", file=sys.stderr)
        else:
            print(_myco.git("-C", target, "log", "--oneline", "-1").stdout.strip())
    n, t, d_ = lay["notes"], lay["tools"], lay["docs"]
    print(f"""
Next steps:
  1. Edit {n}/working-agreement.md and {n}/state.md (headline facts, components).
  2. Set MIRROR_TARGET in .myco-notes.conf and run {t}/mirror-push (then a timer every few hours).
  3. Set up an independent encrypted snapshot of the mirror and run {t}/restore-drill once.
  4. python3 {t}/build-notes-map.py   (navigable map with backlinks and history)
  5. Commit after every meaningful update:  {t}/notes-commit "what changed and why"
     (New folders and files in the root: NEW_FOLDERS={a.new_folders} in .myco-notes.conf; with 'ask' notes-commit stops and lists them.)
  6. Sharing with a team (GitHub or GitHub Enterprise): see {d_}/TEAM.md; teammates run {t}/setup-clone after cloning.
  (On Windows in cmd or PowerShell write {t.replace('/', chr(92))}\\notes-commit; in Git Bash, WSL, macOS and Linux write {t}/notes-commit.)""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
