#!/usr/bin/env python3
"""doctor: read-only health check of a Myco Notes tree. It changes nothing.

  tools/doctor [--root DIR]

Run it from the tree's own tools to check the tree, or from a newer kit (python3 <KIT>/tools/doctor.py --root PROJECT) to
also see whether the tree's tools are older than that kit.

Checks
  version   KIT_VERSION in .myco-notes.conf, the version of the tree's tools, and (from a kit) the kit's version
  layout    the notes, tools and docs folders named in .myco-notes.conf exist; the master record, tracker and entry point exist
  hook      git uses the versioned hook (core.hooksPath) and the hook file is there
  tools     every tool has its sh and .cmd launcher
  settings  settings newer kits expect (NEW_FOLDERS, MIRROR_SCOPE, KIT_VERSION) and the generated map being gitignored
Exit: 0 healthy, 1 something to fix (each line says what), 2 not a Myco Notes tree.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import _myco  # noqa: E402

TOOLS = ("asof", "ci-check", "doctor", "mirror-push", "notes-commit", "relocate", "restore-drill", "resume",
         "secret-scan", "setup-clone")


def tools_version(tools_dir):
    try:
        with open(os.path.join(tools_dir, "_myco.py"), encoding="utf-8") as fh:
            m = re.search(r'^VERSION = "([^"]+)"', fh.read(), re.M)
        return m.group(1) if m else None   # tools older than 0.12.0 do not carry a version
    except OSError:
        return None


def vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


def main():
    _myco.utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    a = ap.parse_args()
    root = _myco.toplevel(os.path.abspath(a.root))
    if not root or not os.path.isfile(os.path.join(root, ".myco-notes.conf")):
        print(f"doctor: {os.path.abspath(a.root)} is not a Myco Notes tree (no .myco-notes.conf at a git repository root)")
        return 2
    conf = _myco.load_conf(root)
    lay = _myco.layout(root, conf)
    problems = 0

    def ok(text):
        print(f"  ok    {text}")

    def fix(text):
        nonlocal problems
        problems += 1
        print(f"  FIX   {text}")

    print(f"doctor: {root}")
    tools_dir = os.path.join(root, *lay["tools"].split("/"))
    recorded = conf.get("KIT_VERSION", "").strip()
    have = tools_version(tools_dir)
    from_kit = _myco.overlap(tools_dir, HERE) != "same"
    print(f"  tree tools: {have or 'older than 0.12.0 (no version inside)'}; recorded KIT_VERSION: {recorded or 'not set'}"
          + (f"; this kit: {_myco.VERSION}" if from_kit else ""))
    if not recorded:
        fix("KIT_VERSION is not set in .myco-notes.conf: refresh the tools with relocate from the current kit (it records it)")
    elif have and recorded != have:
        fix(f"KIT_VERSION says {recorded} but the tools are {have}: the tools were copied by hand; refresh them with relocate")
    if from_kit and vkey(have) < vkey(_myco.VERSION):
        fix(f"the tree's tools ({have or 'before 0.12.0'}) are older than this kit ({_myco.VERSION}): "
            f"python3 {os.path.join(HERE, 'relocate.py')} --root {root}  (dry run first, then --apply)")
    elif not from_kit or have:
        ok("tools version")

    for what, rel in (("notes folder", lay["notes"]), ("tools folder", lay["tools"]), ("docs folder", lay["docs"])):
        (ok if os.path.isdir(os.path.join(root, *rel.split("/"))) else fix)(f"{what} {rel}/" + ("" if os.path.isdir(os.path.join(root, *rel.split("/"))) else " is missing"))
    for what, rel in (("entry point", "AGENTS.md"), ("master record", lay["notes"] + "/" + lay["state"]),
                      ("tracker", lay["notes"] + "/sync-tracker.md")):
        present = os.path.isfile(os.path.join(root, *rel.split("/")))
        (ok if present else fix)(f"{what} {rel}" + ("" if present else " is missing"))

    hp = _myco.git("-C", root, "config", "core.hooksPath").stdout.strip()
    want = lay["tools"] + "/hooks"
    if hp != want:
        fix(f"git does not use the versioned hook (core.hooksPath is {hp or 'not set'}, expected {want}): run {lay['tools']}/setup-clone")
    elif not os.path.isfile(os.path.join(root, *want.split("/"), "pre-commit")):
        fix(f"{want}/pre-commit is missing: refresh the tools with relocate")
    else:
        ok("secret-refusing hook active")

    missing = []
    for t in TOOLS:
        for name in (t, t + ".cmd"):
            if not os.path.isfile(os.path.join(tools_dir, name)) and not (t == "doctor" and have is None):
                missing.append(name)
    if missing:
        fix(f"launchers missing in {lay['tools']}/: {', '.join(missing)}")
    else:
        ok("launchers present")

    for key, why in (("NEW_FOLDERS", "what notes-commit does with a new root folder (ask, add, ignorelint, ignoreall)"),
                     ("MIRROR_SCOPE", "what a mirror copies (tracked recommended: never ignored or untracked files)")):
        if key in conf:
            ok(f"{key}={conf[key]}")
        else:
            fix(f"{key} is not set in .myco-notes.conf: {why}")
    if _myco.git("-C", root, "check-ignore", "-q", lay["map"] + "/x").returncode == 0:
        ok(f"generated map {lay['map']}/ is gitignored")
    else:
        fix(f"the generated map {lay['map']}/ is not in .gitignore")
    if not os.path.isfile(os.path.join(root, ".gitattributes")):
        fix(".gitattributes is missing (line endings may differ between Windows and other systems)")

    print(f"doctor: {'healthy' if not problems else str(problems) + ' thing(s) to fix'} (nothing was changed)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
