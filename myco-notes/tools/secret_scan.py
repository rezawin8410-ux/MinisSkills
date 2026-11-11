#!/usr/bin/env python3
"""secret_scan: read a unified diff on stdin and report ADDED lines that look like secrets. Used by hooks/pre-commit
(staged changes) and by ci_check (each commit of a pull request), and its RULES are the ones notes-lint and the map
builder use, so every check applies the same rules.

    git diff --cached -U0 | tools/secret-scan
    git diff -U0 origin/main...HEAD | tools/secret-scan

Flags: private-key blocks, cloud/API token prefixes; password/passphrase/psk followed by ':' or '=' and a literal of 12+
characters (12 to 19 only when not a plain lower-case word); secret/token/api_key followed by a literal of 20+
characters. Names match in any case (PASSWORD= too). Prompts like printf "...%s", placeholders like <password> or ${PASS}
and references like "!secret name" do not match. Pattern-based: an unusual format can pass, so treat this as a safety net,
not a guarantee.
Reports name the file, line and rule and show no text from the line at all (a line can hold more than one secret, and a
second one in a format the rules do not know would otherwise be printed), so a report never copies a secret into a terminal
or CI log. Open the file at that line to see it.
Allow list: regular expressions (one per line) in the file named by SECRET_ALLOW_FILE (default .myco-notes-secret-allow);
each is matched against "path:line: rule" and against the line itself. An invalid expression is an error (exit 3).
Exit: 0 nothing found, 1 hits, 3 the scan could not run.
"""
import os
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _myco  # noqa: E402

_VAL = r"[\"']?\s*[:=]\s*[\"']?"
RULES = [
    ("private key block", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("API key (sk-)", re.compile(r"sk-[A-Za-z0-9]{32,}")),
    ("password", re.compile(r"(?i)(?<![A-Za-z])(password|passwd|passphrase|psk)" + _VAL + r"(?P<v>[^\s\"'`<>${}%]{12,})")),
    ("secret or token", re.compile(r"(?i)(secret|token|api[_-]?key)" + _VAL + r"(?P<v>[A-Za-z0-9+/_=.-]{20,})")),
]
PLACEHOLDER = re.compile(r"(?i)change.?me|example|placeholder|redacted|your[_-]|x{6,}|\*{4,}")


class ScanError(Exception):
    """The scan could not run (for example an invalid allow-list expression)."""


def find(line):
    """(rule name, (start, end) of the value) for the first rule that matches `line`, or None."""
    for name, rx in RULES:
        for m in rx.finditer(line):
            if "v" in rx.groupindex:
                v = m.group("v")
                if PLACEHOLDER.search(v):
                    continue
                if name == "password" and len(v) < 20 and re.fullmatch(r"[a-z_-]+", v):   # a short plain word, not a password
                    continue
                return name, m.span("v")
            return name, m.span()
    return None


def compile_allow(lines, source="allow list"):
    rx = []
    for n, pat in enumerate(lines, 1):
        try:
            rx.append(re.compile(pat))
        except re.error as e:
            raise ScanError(f"{source}: invalid regular expression on entry {n} ({pat!r}): {e}")
    return rx


def read_allow(root, conf):
    path = os.path.join(root, conf.get("SECRET_ALLOW_FILE") or ".myco-notes-secret-allow")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return compile_allow([l.strip() for l in fh if l.strip() and not l.startswith("#")], os.path.basename(path))


def scan(diff_text, allow=()):
    """Findings for the ADDED lines of a unified diff: a list of (path, line number, rule). No text of the line is kept.
    `allow` holds compiled expressions (or strings) matched against 'path:line: rule' and the line itself."""
    allow = [a if hasattr(a, "search") else re.compile(a) for a in allow]
    hits = []
    path, ln = "?", 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            path = p[2:] if p.startswith("b/") else p
            continue
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            ln = int(m.group(1)) if m else 0
            continue
        if line.startswith("+"):
            body = line[1:]
            f = find(body)
            if f:
                tag = f"{path}:{ln}: {f[0]}"
                if not any(a.search(tag) or a.search(body) for a in allow):
                    hits.append((path, ln, f[0]))
            ln += 1
        elif line.startswith(" "):
            ln += 1
    return hits


def format_hit(hit, prefix=""):
    path, ln, rule = hit
    return f"{prefix}{path}:{ln}: {rule} (value not shown)"


def main():
    _myco.utf8_console()
    try:
        root = _myco.toplevel(os.getcwd()) or os.getcwd()
        allow = read_allow(root, _myco.load_conf(root))
        diff_text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        hits = scan(diff_text, allow)
    except ScanError as e:
        print(f"secret-scan: could not run: {e}")
        return 3
    except Exception as e:  # never let a crash look like "nothing found"
        print(f"secret-scan: could not run: {type(e).__name__}: {e}")
        return 3
    for h in hits:
        print(format_hit(h))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
