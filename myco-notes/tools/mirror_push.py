#!/usr/bin/env python3
"""mirror-push: keep a second copy of the notes repository on another machine or disk.

Target comes from MIRROR_TARGET in .myco-notes.conf:
  /path/or/C:\\path     a local directory: the whole working copy including .git is copied and the copy is made equal
                        (files that no longer exist are removed there)
  git:URL               a git remote (any URL git understands, including a bare repository on another disk or server):
                        `git push --mirror` (history and tracked files only; needs no rsync, works the same on Windows)
  user@host:/path       another machine over ssh with rsync (needs ssh and rsync installed)
History safety comes from an independent snapshot job that backs up the mirror. Skips quietly (exit 0) when the target is
unreachable or not configured, so a timer never nags. Run it from a timer (for example every 6 hours) and after notes-commit -p.

  mirror-push --dry-run    for a local directory: list what would be copied and removed, change nothing
  mirror-push --strict     scheduled-job mode (or MIRROR_STRICT=1): exit 3 when skipped, 1 when anything failed

  mirror-push --verify     compare file contents (SHA-256) when sizes match, not only size and time (or MIRROR_VERIFY=hash)

Links: a symbolic link or Windows junction found in the target is removed (never followed), so a sync cannot reach outside
the target; links in the repository are copied as links. A linked git worktree (.git is a file pointing elsewhere) cannot be
copied into a self-contained mirror: use MIRROR_TARGET=git:URL there. An ssh target gets the same ownership check as a local
one before rsync deletes anything.

Result: the last line starts with "mirror: synced", "mirror: skipped" or "mirror: failed".
Exit: 0 synced (or skipped, without --strict), 1 failed, 3 skipped with --strict.
MIRROR_SCOPE (local directory and ssh targets): tracked = .git plus the tracked files, never ignored or untracked files (new
trees); all = the whole working copy (the default when the setting is missing, as in trees made before 0.12.0). Files outside
the scope that an earlier sync copied are removed from the target.

A local directory target must not be the repository, a folder inside it or a folder that contains it. Because the copy is
made equal (files only in the target are removed), the tool writes only into an empty folder, a folder holding its marker
file .myco-mirror, or an earlier mirror of the same repository (it then adds the marker).
"""
import os
import re
import shutil
import stat
import subprocess
import sys
import hashlib
import shlex
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import _myco  # noqa: E402


def link_like(path):
    """True for a symbolic link or a Windows junction (or other reparse point): something a sync must not follow."""
    if os.path.islink(path):
        return True
    isj = getattr(os.path, "isjunction", None)          # Python 3.12+
    if isj and isj(path):
        return True
    try:
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)   # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return False


def _rm(path):
    if link_like(path):                                 # remove the link itself, never what it points to
        try:
            os.unlink(path)
        except OSError:
            os.rmdir(path)                              # a junction is removed with rmdir
        return
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)  # read-only files (.git objects on Windows)
    except OSError:
        pass
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, onerror=lambda f, p, e: (os.chmod(p, stat.S_IWRITE), f(p)))
    else:
        os.remove(path)


MARKER = ".myco-mirror"


def same_content(a, b):
    def h(p):
        d = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                d.update(chunk)
        return d.digest()
    return h(a) == h(b)


def sync_dir(src, dst, plan=None, top=True, keep=None, rel="", verify=False, base=None, empty=False):
    """Make dst equal to src (like rsync -a --delete): copy new or changed files, remove what no longer exists.
    With `plan` (a list) nothing changes: each action is appended as ("remove"|"copy", path) instead.
    `keep(relative path)` limits what counts as source (MIRROR_SCOPE=tracked); anything else is removed from dst."""
    base = base or dst
    if top and plan is None:
        os.makedirs(dst, exist_ok=True)
    if not top and not empty and os.path.lexists(dst) and _myco.overlap(base, dst) != "inside":
        raise OSError(f"refusing to write {dst}: it resolves outside the mirror {base}")
    src_names = set(os.listdir(src))
    if keep:
        src_names = {n for n in src_names if keep(rel + n)}
    dst_names = os.listdir(dst) if (os.path.isdir(dst) and not empty) else []
    for name in dst_names:
        if name not in src_names and not (top and name == MARKER):
            if plan is None:
                _rm(os.path.join(dst, name))
            else:
                plan.append(("remove", os.path.join(dst, name)))
    for name in sorted(src_names):
        s, d = os.path.join(src, name), os.path.join(dst, name)
        dlink = os.path.lexists(d) and link_like(d)
        if plan is not None:
            if dlink:
                plan.append(("remove link", d))
            if os.path.isdir(s) and not link_like(s):
                if os.path.lexists(d) and not dlink and not os.path.isdir(d):
                    plan.append(("remove", d))
                sync_dir(s, d, plan, False, keep, rel + name + "/", verify, base, empty=dlink)
            elif dlink or not (os.path.isfile(d) and os.stat(d).st_size == os.stat(s).st_size
                               and (same_content(s, d) if verify else int(os.stat(d).st_mtime) == int(os.stat(s).st_mtime))):
                plan.append(("copy", d))
            continue
        if dlink:
            _rm(d)                                      # never follow a link in the mirror
        if link_like(s):
            if os.path.lexists(d):
                _rm(d)
            try:
                os.symlink(os.readlink(s), d)
            except (OSError, NotImplementedError):
                pass
        elif os.path.isdir(s):
            if os.path.lexists(d) and not os.path.isdir(d):
                _rm(d)
            os.makedirs(d, exist_ok=True)
            sync_dir(s, d, None, False, keep, rel + name + "/", verify, base)
        else:
            if os.path.isdir(d) and not os.path.islink(d):
                _rm(d)
            ss = os.stat(s)
            if os.path.exists(d):
                ds = os.stat(d)
                if ds.st_size == ss.st_size and (same_content(s, d) if verify else int(ds.st_mtime) == int(ss.st_mtime)):
                    continue
                if not os.access(d, os.W_OK):
                    os.chmod(d, stat.S_IWRITE | stat.S_IREAD)
            shutil.copy2(s, d)


def same_repository(root, target):
    """True when target is a git working copy that shares its first commit with root (an earlier mirror of it)."""
    if not os.path.isdir(os.path.join(target, ".git")):
        return False
    a = _myco.git("-C", root, "rev-list", "--max-parents=0", "HEAD").stdout.split()
    b = _myco.git("-C", target, "rev-list", "--max-parents=0", "HEAD").stdout.split()
    return bool(a) and bool(set(a) & set(b))


def check_local_target(root, target):
    """None when the local target is safe to make equal to root, else the reason it is refused."""
    rel = _myco.overlap(root, target)
    if rel == "same":
        return "the target is the repository itself"
    if rel == "inside":
        return "the target is inside the repository (it would copy itself)"
    if rel == "contains":
        return "the target contains the repository (making it equal would delete everything else in it)"
    if not _myco.owned_or_empty(target, MARKER, lambda t: same_repository(root, t)):
        return (f"{target} is not empty and is not a mirror of this repository; point MIRROR_TARGET at an empty folder, or put an "
                f"empty file named {MARKER} in it if every file there may be replaced or removed")
    return None


def tracked_filter(root):
    """keep(rel) for MIRROR_SCOPE=tracked: .git and everything under it, the tracked files and the folders that hold them."""
    files = [f for f in _myco.git("-C", root, "-c", "core.quotepath=off", "ls-files", "-z").stdout.split("\0") if f]
    dirs = set()
    for f in files:
        parts = f.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    files = set(files)
    return lambda r: r == ".git" or r.startswith(".git/") or r in files or r in dirs


def check_remote_target(root, host, path):
    """The ssh twin of check_local_target: None when user@host:path is missing, empty, holds our marker, or is a mirror of
    this repository (same first commit); otherwise the reason. Needs a POSIX shell on the remote side."""
    roots = " ".join(_myco.git("-C", root, "rev-list", "--max-parents=0", "HEAD").stdout.split()) or "none"
    q = shlex.quote(path.rstrip("/") or "/")
    script = (f"p={q}; [ -e \"$p\" ] || {{ echo ok; exit; }}; [ -d \"$p\" ] || {{ echo notdir; exit; }}; "
              f"[ -z \"$(ls -A \"$p\")\" ] && {{ echo ok; exit; }}; [ -f \"$p/{MARKER}\" ] && {{ echo ok; exit; }}; "
              f"r=$(git -C \"$p\" rev-list --max-parents=0 HEAD 2>/dev/null); "
              f"for c in {roots}; do case \" $r \" in *\" $c \"*|*\"$c\"*) echo ok; exit;; esac; done; echo foreign")
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if r.returncode != 0 or out not in ("ok", "notdir", "foreign"):
        return f"could not check {host}:{path} before syncing ({(r.stderr.strip().splitlines() or ['no answer'])[-1]})"
    if out == "notdir":
        return f"{host}:{path} exists and is not a folder"
    if out == "foreign":
        return (f"{host}:{path} is not empty and is not a mirror of this repository; point MIRROR_TARGET at an empty folder, or "
                f"create an empty file {MARKER} in it if every file there may be replaced or removed")
    return None


def dir_size(path):
    total = 0
    for dp, _dn, fn in os.walk(path):
        for f in fn:
            try:
                total += os.lstat(os.path.join(dp, f)).st_size
            except OSError:
                pass
    return total


def main():
    _myco.utf8_console()
    root = _myco.toplevel(HERE)
    if not root:
        print("mirror: failed, not inside a git repository", file=sys.stderr)
        return 1
    os.chdir(root)
    args = sys.argv[1:]
    dry = "--dry-run" in args
    conf = _myco.load_conf(root)
    strict = "--strict" in args or conf.get("MIRROR_STRICT", "0") == "1"
    verify = "--verify" in args or conf.get("MIRROR_VERIFY", "").strip().lower() == "hash"
    scope = (conf.get("MIRROR_SCOPE") or "all").strip().lower()
    if scope not in ("tracked", "all"):
        print(f"mirror: failed, MIRROR_SCOPE={scope!r} must be tracked or all", file=sys.stderr)
        return 1
    target = os.path.expanduser(os.path.expandvars(conf.get("MIRROR_TARGET", "")))
    post = [c for c in conf.get("POST_PUSH_CMDS", "").splitlines() if c.strip()]

    def skipped(why):
        print(f"mirror: skipped, {why}")
        return 3 if strict else 0

    def failed(why):
        print(f"mirror: failed, {why}", file=sys.stderr)
        return 1

    if not target:
        return skipped("MIRROR_TARGET not set in .myco-notes.conf")

    if target.startswith("git:"):
        url = target[4:]
        if dry:
            print(f"mirror: dry run, would run git push --mirror to {url}; nothing changed")
            return 0
        reach = subprocess.run(["git", "ls-remote", "-q", "--heads", url], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=60, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        if reach.returncode:
            return skipped(f"{url} not reachable ({reach.stderr.strip().splitlines()[-1] if reach.stderr.strip() else 'no detail'})")
        r = subprocess.run(["git", "push", "--mirror", "-q", url], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            return failed(f"git push --mirror to {url} ({r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'no detail'})")
        what = "history and tracked files"
    elif not os.path.isdir(os.path.join(root, ".git")):
        return failed("this is a linked git worktree (.git is a file pointing to history stored elsewhere), so a copy would not "
                      "hold its own history; use MIRROR_TARGET=git:URL (git push --mirror) for it")
    elif re.match(r"^[A-Za-z]:[\\/]", target) or ":" not in target:
        why = check_local_target(root, target)
        if why:
            return failed(f"refused: {why}")
        keep = tracked_filter(root) if scope == "tracked" else None
        if dry:
            plan = []
            sync_dir(root, target, plan, keep=keep, verify=verify)
            for act, path in plan:
                print(f"  would {act}: {path}")
            print(f"mirror: dry run ({scope}), {sum(a == 'copy' for a, _ in plan)} to copy, {sum(a == 'remove' for a, _ in plan)} to remove; nothing changed")
            return 0
        try:
            sync_dir(root, target, keep=keep, verify=verify)
            open(os.path.join(target, MARKER), "a").close()
        except OSError as e:
            return failed(f"copy to {target} stopped: {e}")
        what = "history and tracked files" if scope == "tracked" else "the whole working copy"
    else:  # user@host:/path
        host = target.split(":", 1)[0]
        if not shutil.which("rsync") or not shutil.which("ssh"):
            return failed("a remote host target needs rsync and ssh; install them or use MIRROR_TARGET=git:URL")
        ok = subprocess.run(["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"], capture_output=True).returncode == 0
        if not ok:
            return skipped(f"{host} not reachable")
        path = target.split(":", 1)[1]
        why = check_remote_target(root, host, path)
        if why:
            return failed(f"refused: {why}")
        cmd = ["rsync", "-a", "--delete", "--filter=P /" + MARKER, "-e", "ssh -o BatchMode=yes"] + (["--checksum"] if verify else [])
        if scope == "tracked":   # leave out (and remove from the target) everything git does not track
            others = [f for f in _myco.git("ls-files", "--others", "--directory", "-z").stdout.split("\0") if f]
            cmd += ["--delete-excluded", "--from0", "--exclude-from=-"]
            listing = "".join("/" + f.rstrip("/") + "\0" for f in others)
        else:
            listing = None
        if dry:
            cmd.insert(1, "--dry-run")
            cmd.insert(1, "--itemize-changes")
        r = subprocess.run(cmd + [root.rstrip("/") + "/", target.rstrip("/") + "/"], input=listing, text=True)
        if r.returncode:
            return failed(f"rsync to {target} exited {r.returncode}")
        if dry:
            print("mirror: dry run, nothing changed")
            return 0
        subprocess.run(["ssh", "-o", "BatchMode=yes", host, "touch " + shlex.quote(path.rstrip("/") + "/" + MARKER)], capture_output=True)
        what = "history and tracked files" if scope == "tracked" else "the whole working copy"

    post_failed = 0
    for c in post:
        try:
            r = _myco.run_shell(c, root, timeout=120)
            print("\n".join((r.stdout + r.stderr).strip().splitlines()[-2:]))
            if r.returncode:
                post_failed += 1
                print(f"mirror: post-push command exited {r.returncode}: {c}")
        except Exception as e:  # a failing publish does not undo the push
            post_failed += 1
            print(f"mirror: post-push command failed ({e})")
    try:
        with open(os.path.join(root, ".git", "last-mirror-push"), "w") as fh:
            fh.write(str(int(time.time())) + "\n")
    except OSError:
        pass
    print(f"mirror: synced, pushed {_myco.human_size(dir_size(root))} ({what}) to {conf.get('MIRROR_TARGET')}"
          + (f"; {post_failed} post-push command(s) failed" if post_failed else ""))
    return 1 if (post_failed and strict) else 0


if __name__ == "__main__":
    sys.exit(main())
