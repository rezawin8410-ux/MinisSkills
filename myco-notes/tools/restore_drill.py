#!/usr/bin/env python3
"""restore-drill: prove the notes can be restored. Give it a directory that was restored from the mirror or from a snapshot
(or a .tar, .tar.gz or .zip of it). It gives two verdicts and prints one line ready to paste into the sync log:
  history       git fsck passes and the commits are there (the notes can be recovered from git)
  working copy  the entry point (AGENTS.md), the master record and the tracker are present, and the working files equal the
                last commit (the restored copy can be used as it is)
Either verdict failing makes the drill fail (exit 1). It also compares the newest commit with the live repository.

  tools/restore-drill /path/to/restored-copy
  tools/restore-drill /path/to/backup.tar.gz
"""
import datetime
import os
import sys
import tarfile
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import _myco  # noqa: E402


def extract(archive, dest):
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)  # zipfile already refuses absolute paths and ..
    else:
        with tarfile.open(archive) as t:
            try:
                t.extractall(dest, filter="data")  # Python 3.12+ and recent patch releases: refuses unsafe members
            except TypeError:
                base = os.path.realpath(dest)
                for m in t.getmembers():
                    if not os.path.realpath(os.path.join(dest, m.name)).startswith(base + os.sep) or m.issym() or m.islnk():
                        raise SystemExit(f"FAIL: unsafe path in archive: {m.name}")
                t.extractall(dest)


def find_repo(src, depth=3):
    src = os.path.abspath(src)
    for dp, dn, _fn in os.walk(src):
        if dp[len(src):].count(os.sep) >= depth:
            dn[:] = []
        if ".git" in dn:
            return dp
    return None


def main():
    _myco.utf8_console()
    if len(sys.argv) < 2:
        print("usage: restore-drill <restored dir | archive>", file=sys.stderr)
        return 2
    arg = sys.argv[1]
    live = _myco.toplevel(HERE)
    tmp = None
    src = arg
    if os.path.isfile(arg):
        tmp = tempfile.TemporaryDirectory()
        extract(arg, tmp.name)
        src = tmp.name
    repo = find_repo(src)
    if not repo:
        print(f"FAIL: no .git found under {src}")
        return 1
    rc = 0
    print(f"restored repository: {repo}")
    r = _myco.git("-C", repo, "fsck", "--no-progress")
    for line in (r.stdout + r.stderr).splitlines():
        if not line.startswith("dangling"):
            print(f"  fsck: {line}")
    if r.returncode == 0:
        print("  fsck: OK")
    else:
        print("  fsck: PROBLEMS")
        rc = 1
    print("  last commits:")
    for line in _myco.git("-C", repo, "log", "--oneline", "-5").stdout.splitlines():
        print(f"    {line}")
    hist_ok = rc == 0 and _myco.git("-C", repo, "rev-parse", "-q", "--verify", "HEAD").returncode == 0
    lay = _myco.layout(repo)
    work = []
    need = [("entry point", "AGENTS.md"), ("master record", lay["notes"] + "/" + lay["state"]), ("tracker", lay["notes"] + "/sync-tracker.md")]
    for what, rel in need:
        if os.path.isfile(os.path.join(repo, *rel.split("/"))):
            print(f"  {what} present: {rel}")
        else:
            print(f"  {what} MISSING: {rel}")
            work.append(f"{rel} missing")
    st = _myco.git("-C", repo, "-c", "core.fileMode=false", "status", "--porcelain", "--untracked-files=no")  # archives drop the executable bit
    changed = [l for l in st.stdout.splitlines() if l.strip()]
    if st.returncode != 0:
        work.append("git status failed")
    elif changed:
        print(f"  working files differ from the last commit ({len(changed)}):")
        for l in changed[:10]:
            print(f"    {l}")
        if len(changed) > 10:
            print(f"    ... and {len(changed) - 10} more")
        work.append(f"{len(changed)} file(s) differ from the last commit")
    print(f"  verdict history:      {'OK' if hist_ok else 'PROBLEMS'}")
    print(f"  verdict working copy: {'OK' if not work else 'INCOMPLETE (' + '; '.join(work) + ')'}")
    if work:
        print(f"  (the history is {'intact: git -C <restored copy> restore --source=HEAD -- . brings the files back' if hist_ok else 'damaged too'})")
        rc = 1
    a = _myco.git("-C", repo, "rev-parse", "HEAD").stdout.strip()
    b = _myco.git("-C", live, "rev-parse", "HEAD").stdout.strip() if live else ""
    if a == b:
        print(f"  restored HEAD equals live HEAD ({a})")
    else:
        print(f"  restored HEAD differs from live HEAD (restored {a[:8]}, live {b[:8]}): fine for an old snapshot, check the date")
    now = datetime.datetime.now()
    wres = "OK" if not work else "INCOMPLETE"
    print()
    print(f"Sync-log line: - **{now:%Y-%m-%d} ({now:%H:%M}):** restore drill from {os.path.basename(arg.rstrip('/' + os.sep))}: history {'OK' if hist_ok else 'PROBLEMS'}, working copy {wres}; HEAD {a[:8]}.")
    if tmp:
        tmp.cleanup()
    return rc


if __name__ == "__main__":
    sys.exit(main())
