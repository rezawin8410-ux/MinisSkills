#!/usr/bin/env python3
"""ci-check: the checks a pull request must pass (run by .github/workflows/notes-check.yml, runnable locally).

  tools/ci-check [BASE]      BASE = commit or ref to compare with (default origin/main); empty or all zeros (a first push) =
                             every commit reachable from HEAD. A BASE that cannot be found fails the check (fetch the full
                             history, fetch-depth: 0); it never quietly scans less.

  1. secret scan of every line each commit in the range adds, and of the range as a whole (same rules as the local hook,
     which a contributor can bypass). Each commit is scanned on its own because a secret added in one commit and removed
     in a later one never shows in the final diff but stays readable in the history. Merge commits count: the lines a merge
     adds that none of its parents had (a conflict resolution) are scanned. Findings show file, line and rule, no text.
  2. tools/notes-lint.py: errors (exit 2) fail the check; warnings fail only when CI_LINT_STRICT=1 in .myco-notes.conf.
     A lint that crashes or does not print its closing summary fails the check: an unfinished check is never a pass.
Exit: 0 passed, 1 failed (a finding, a lint error, or a check that could not run).
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import _myco  # noqa: E402
import secret_scan  # noqa: E402

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def commit_hits(c, allow):
    """Findings for what commit c adds. For a merge: only lines added relative to every parent (what the merge itself
    introduced, such as a conflict resolution); lines that came from a parent are scanned in that parent's own commit."""
    parents = _myco.git("rev-list", "--parents", "-n", "1", c).stdout.split()[1:]
    if len(parents) <= 1:
        diff = _myco.git("diff", "-U0", "--no-color", parents[0] if parents else EMPTY_TREE, c)
        if diff.returncode != 0:
            raise RuntimeError(f"git diff for {c[:8]} failed: {diff.stderr.strip()[:200]}")
        return secret_scan.scan(diff.stdout, allow)
    common = None
    for p in parents:
        diff = _myco.git("diff", "-U0", "--no-color", p, c)
        if diff.returncode != 0:
            raise RuntimeError(f"git diff for merge {c[:8]} failed: {diff.stderr.strip()[:200]}")
        found = set(secret_scan.scan(diff.stdout, allow))
        common = found if common is None else common & found
    return sorted(common or [])


def main():
    _myco.utf8_console()
    root = _myco.toplevel(HERE)
    if not root:
        print("ci-check: not inside a git repository", file=sys.stderr)
        return 1
    os.chdir(root)
    conf = _myco.load_conf(root)
    strict = conf.get("CI_LINT_STRICT", "0") == "1"

    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    if base.strip("0") == "":
        base = ""
    if base and _myco.git("rev-parse", "-q", "--verify", f"{base}^{{commit}}").returncode != 0:
        shallow = _myco.git("rev-parse", "--is-shallow-repository").stdout.strip() == "true"
        print(f"ci-check: FAILED, base '{base}' not found" + (" (this is a shallow clone: check out with fetch-depth: 0)" if shallow else
              " (after a force push the old commit is gone: run ci-check with an empty base to scan the whole history)"))
        return 1
    if not base and _myco.git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        print("ci-check: FAILED, a first push is scanned over the whole history, but this is a shallow clone (fetch-depth: 0)")
        return 1
    rng = [f"{base}...HEAD"] if base else [EMPTY_TREE, "HEAD"]
    rc = 0
    commits = _myco.git("rev-list", "--reverse", f"{base}..HEAD" if base else "HEAD").stdout.split()
    print(f"== secret scan ({len(commits)} commit(s), and {' '.join(rng)})")
    try:
        allow = secret_scan.read_allow(root, conf)
        hits, seen = [], set()
        for c in commits:
            for h in commit_hits(c, allow):
                hits.append(secret_scan.format_hit(h, f"{c[:8]} "))
                seen.add(h)
        r = _myco.git("diff", "-U0", "--no-color", *rng)
        if r.returncode != 0:
            raise RuntimeError(f"git diff failed: {r.stderr.strip()[:200]}")
        hits += [secret_scan.format_hit(h, "range ") for h in secret_scan.scan(r.stdout, allow) if h not in seen]
    except Exception as e:
        print(f"FAIL: the secret scan could not run: {e}")
        hits, rc = [], 1
    if hits:
        print("FAIL: possible secrets in added lines (values hidden). A secret found in an earlier commit is still in the history:")
        print("      change the credential, then remove it from the branch history before merging (method 8.1).")
        for h in dict.fromkeys(hits):
            print("  " + h)
        rc = 1
    elif rc == 0:
        print("ok: no secret-looking added lines")

    print("== notes-lint")
    sys.stdout.flush()
    lr = subprocess.run([sys.executable, os.path.join(HERE, "notes-lint.py"), "--root", root], capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    sys.stdout.write(lr.stdout)
    sys.stderr.write(lr.stderr)
    lrc = lr.returncode
    finished = bool(re.search(r"(?m)^notes-lint: \d+ notes, \d+ errors, \d+ warnings, \d+ info$", lr.stdout))
    if lrc not in (0, 1, 2) or not finished:
        print(f"FAIL: the lint did not finish (exit {lrc}); fix the cause above")
        rc = 1
    elif lrc == 2:
        print("FAIL: lint errors")
        rc = 1
    elif lrc == 1 and strict:
        print("FAIL: lint warnings (CI_LINT_STRICT=1)")
        rc = 1
    elif lrc == 1:
        print("warnings only: not failing (set CI_LINT_STRICT=1 to fail on warnings)")

    print("ci-check: passed" if rc == 0 else "ci-check: FAILED")
    return rc


if __name__ == "__main__":
    sys.exit(main())
