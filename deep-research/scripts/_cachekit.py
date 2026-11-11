#!/usr/bin/env python3
"""
cachectl.py — one cache root for every skill in the pack.

Before this, each script grew its own directory under `~/.cache/agent-skills`
and each one knew its own TTL. That is fine until you want to know how much disk
the pack is using, or wipe a corrupt fetch cache without touching a 63 MB Piper
voice you waited for. So: one root, namespaces inside it, and one tool that can
describe and clean the whole thing.

    <root>/search/<sha1>.json      web-search, deep-research   1 h  TTL
    <root>/fetch/<sha1>.json       fetch-page, deep-research   24 h TTL
    <root>/piper/*.onnx            anon-audio (offline voices) never pruned
    <root>/kv/<ns>/<key>.json      anything else (generic store)

    root = $AGENT_SKILLS_CACHE, else ~/.cache/agent-skills

Usage:
    cachectl.py stats                 # what is in there, per namespace
    cachectl.py path                  # print the root
    cachectl.py prune                 # drop entries older than --days (24)
    cachectl.py clear search          # wipe one namespace
    cachectl.py clear all             # wipe everything except voices
    cachectl.py get <ns> <key>        # generic key/value store
    cachectl.py put <ns> <key>        # JSON on stdin
    cachectl.py put <ns> <key> --ttl 3600

Exit codes: 0 ok · 2 bad usage

The same file is vendored into each skill that caches (as `_cachekit.py`), so a
skill installed on its own can still say `python3 scripts/_cachekit.py stats`.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import threading
import time

DEFAULT_ROOT = os.path.expanduser("~/.cache/agent-skills")
PROTECTED = {"piper"}                      # model files, never auto-pruned
TMP_PREFIX = ".tmp-"                       # half-written entries: never read, always GC'd
TTL_HINT = {"search": 3600, "fetch": 86400}


def root():
    return os.path.abspath(os.environ.get("AGENT_SKILLS_CACHE") or DEFAULT_ROOT)


def ns_dir(ns, create=False):
    p = os.path.join(root(), ns)
    if create:
        os.makedirs(p, exist_ok=True)
    return p


def _files(ns):
    d = ns_dir(ns)
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d))
            if os.path.isfile(os.path.join(d, f))]


def _age_days(path):
    try:
        return (time.time() - os.path.getmtime(path)) / 86400.0
    except OSError:
        return 0.0


def _entry_ts(path):
    """Prefer the timestamp the entry carries inside it (`_ts`) over mtime."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("_ts"), (int, float)):
            return d["_ts"]
    except Exception:
        pass
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


# ------------------------------------------------------------- argparse helpers
# A CLI that accepts `--parallel 0` does not fail politely, it fails inside
# ThreadPoolExecutor with a ValueError traceback — and `--max-chars -5` does
# something worse: slicing with a negative index silently *drops the end of the
# page* and reports success. Both are input validation, so it belongs here, in
# the one module every CLI in the pack already ships.
def pos_int(text):
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more (got {n})")
    return n


def nonneg_int(text):
    n = int(text)
    if n < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more (got {n})")
    return n


def pos_float(text):
    x = float(text)
    if not x > 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0 (got {text})")
    return x


# -------------------------------------------------------------- safe primitives
# Every cache writer in the pack goes through these two functions. Writing
# straight to the final path means a reader can open a file that is still being
# written and get half a JSON document; a crash mid-write leaves that truncated
# entry behind to be re-read until its TTL runs out. Write to a sibling temp file
# and os.replace() it: rename is atomic on POSIX, so a reader sees either the old
# entry or the new one, never a torn one.
def tmp_name(path):
    return os.path.join(os.path.dirname(path) or ".",
                        f"{TMP_PREFIX}{os.getpid()}-{threading.get_ident()}-"
                        f"{os.path.basename(path)}")


def atomic_write_json(path, obj, **dump_kw):
    """Write *obj* as JSON at *path* so no reader can ever see a partial file."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = tmp_name(path)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, **dump_kw)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def read_json(path, default=None):
    """json.load() that never raises and never leaks the handle."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ------------------------------------------------------------------ generic kv
def key_path(ns, key):
    safe = hashlib.sha1(key.encode()).hexdigest()[:16]
    return os.path.join(ns_dir(ns, create=True), f"{safe}.json")


def get(ns, key, ttl=None):
    p = key_path(ns, key)
    d = read_json(p)
    if not isinstance(d, dict):
        return None
    if ttl and time.time() - d.get("_ts", 0) > ttl:
        return None
    return d.get("value")


def put(ns, key, value, ttl=None):
    d = {"key": key, "value": value, "_ts": time.time()}
    if ttl:
        d["_ttl"] = ttl
    return atomic_write_json(key_path(ns, key), d)


# ------------------------------------------------------------------ inspection
def stats(as_json=False):
    rows = []
    r = root()
    if os.path.isdir(r):
        for ns in sorted(os.listdir(r)):
            d = os.path.join(r, ns)
            if not os.path.isdir(d):
                continue
            files = _files(ns)
            size = sum(os.path.getsize(f) for f in files)
            ages = [_age_days(f) for f in files] or [0]
            rows.append({"namespace": ns, "files": len(files), "bytes": size,
                         "mb": round(size / 1048576, 2),
                         "oldest_days": round(max(ages), 2),
                         "protected": ns in PROTECTED,
                         "ttl_hint": TTL_HINT.get(ns)})
    if as_json:
        print(json.dumps({"root": r, "namespaces": rows,
                          "total_mb": round(sum(x["bytes"] for x in rows) / 1048576, 2)},
                         indent=1))
        return 0
    if not rows:
        print(f"{r}: empty")
        return 0
    print(f"{r}\n")
    print(f"{'namespace':<12}{'files':>7}{'size':>10}{'oldest':>9}  note")
    for x in rows:
        note = "model files, never pruned" if x["protected"] else (
            f"{x['ttl_hint']}s ttl" if x["ttl_hint"] else "generic store")
        print(f"{x['namespace']:<12}{x['files']:>7}{x['mb']:>9}M{x['oldest_days']:>8}d  {note}")
    total = sum(x["bytes"] for x in rows) / 1048576
    print(f"\n{sum(x['files'] for x in rows)} files, {total:.2f} MB")
    return 0


def prune(days=1.0, dry_run=False):
    removed, freed, kept = 0, 0, 0
    cutoff = time.time() - days * 86400
    r = root()
    if not os.path.isdir(r):
        print(f"{r}: nothing to prune")
        return 0
    for ns in sorted(os.listdir(r)):
        if ns in PROTECTED or not os.path.isdir(os.path.join(r, ns)):
            continue
        for f in _files(ns):
            if os.path.basename(f).startswith(TMP_PREFIX):
                # debris from a writer that died between open() and replace():
                # no process can still own a temp file this old
                if _age_days(f) * 86400 > 3600:
                    if not dry_run:
                        try:
                            os.unlink(f)
                        except OSError:
                            pass
                    removed += 1
                else:
                    kept += 1
                continue
            if not f.endswith(".json"):        # never touch binaries
                kept += 1
                continue
            if _entry_ts(f) < cutoff:
                size = os.path.getsize(f)
                if not dry_run:
                    try:
                        os.unlink(f)
                    except OSError:
                        kept += 1
                        continue
                removed += 1
                freed += size
    verb = "would remove" if dry_run else "removed"
    print(f"{verb} {removed} entries, {freed/1048576:.2f} MB freed"
          + (f", {kept} kept" if kept else ""))
    return 0


def clear(ns):
    r = root()
    targets = ([d for d in sorted(os.listdir(r)) if os.path.isdir(os.path.join(r, d))]
               if ns in (None, "all") else [ns])
    n = 0
    for t in targets:
        if t in PROTECTED:
            print(f"skip {t}: model files, delete by hand if you really mean it")
            continue
        d = os.path.join(r, t)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    print(f"cleared {n} namespace(s)")
    return 0


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="manage the shared agent-skills cache")
    ap.add_argument("cmd", choices=["stats", "path", "prune", "clear", "get", "put"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--days", type=float, default=1.0)
    ap.add_argument("--ttl", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.cmd == "path":
        print(root())
        return 0
    if a.cmd == "stats":
        return stats(a.json)
    if a.cmd == "prune":
        return prune(a.days, a.dry_run)
    if a.cmd == "clear":
        return clear(a.args[0] if a.args else "all")
    if a.cmd == "get":
        if len(a.args) < 2:
            ap.error("get needs <ns> <key>")
        v = get(a.args[0], a.args[1])
        print(json.dumps(v, indent=1) if v is not None else "")
        return 0
    if a.cmd == "put":
        if len(a.args) < 2:
            ap.error("put needs <ns> <key>  (JSON on stdin)")
        raw = sys.stdin.read()
        try:
            v = json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError:
            v = raw.strip()
        print(put(a.args[0], a.args[1], v, a.ttl))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
