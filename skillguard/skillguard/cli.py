"""Static security scanner for untrusted agent skill folders."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

__version__ = "0.1.1"

HIDDEN_CHARS = {
    "\u200b": "zero-width space",
    "\u200c": "zero-width non-joiner",
    "\u200d": "zero-width joiner",
    "\u2060": "word joiner",
    "\ufeff": "BOM / zero-width no-break space",
    "\u202a": "LTR embedding",
    "\u202b": "RTL embedding",
    "\u202c": "pop directional formatting",
    "\u202d": "LTR override",
    "\u202e": "RTL override",
}

IGNORE_RE = re.compile(
    r"ignore (all )?(previous|prior) (instructions|prompts|rules)|disregard (your )?(system )?prompt",
    re.I,
)
EXFIL_HOST_RE = re.compile(
    r"(webhook\.site|requestbin|pipedream\.net|ngrok\.(io|app)|pastebin\.com|discord\.com/api/webhooks|hooks\.slack\.com)",
    re.I,
)
CURL_RE = re.compile(r"\b(curl|wget|invoke-webrequest|fetch)\b", re.I)
SECRET_PATH_RE = re.compile(
    r"(\.env\b|id_rsa|id_ed25519|credentials\.json|aws/credentials|\.npmrc|\.pypirc|netrc|kube/config|gcloud/credentials)",
    re.I,
)
ENV_SECRET_RE = re.compile(
    r"(\$\{?(?:AWS_SECRET_ACCESS_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|GITHUB_TOKEN|GH_TOKEN|STRIPE_SECRET)[A-Z0-9_]*\}?)",
)
BASE64_RUN_RE = re.compile(
    r"(base64\s+-d|base64\.b64decode|decode\(.*base64|eval\(|exec\(|Invoke-Expression|fromcharcode)",
    re.I,
)
LONG_B64_RE = re.compile(r"\b[A-Za-z0-9+/]{200,}={0,2}\b")
SMUGGLE_RE = re.compile(
    r"(after (every|each|the last) (tool|function) call|forward (the )?(full )?(conversation|chat|transcript)|append .{0,40} to (the )?url)",
    re.I,
)

TEXT_GLOBS = {".md", ".txt", ".py", ".js", ".ts", ".sh", ".bash", ".zsh", ".json", ".yml", ".yaml", ".rb", ".go"}
SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "node_modules", ".tox", ".mypy_cache"}
MAX_FILE_BYTES = 2_000_000


@dataclass
class Finding:
    severity: str  # danger, warning, info
    code: str
    message: str
    path: str
    line: int | None = None


def iter_text_files(root: Path) -> list[Path]:
    """Walk a skill tree without following directory symlinks or leaving root."""
    files: list[Path] = []
    root = root.resolve()
    stack = [root]
    seen: set[Path] = set()
    while stack:
        current = stack.pop()
        try:
            resolved = current.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        if current.is_symlink() and current.is_dir():
            continue
        try:
            if resolved != root and not resolved.is_relative_to(root):
                continue
        except ValueError:
            continue
        seen.add(resolved)
        if current.is_dir():
            try:
                children = list(current.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_dir() and not child.is_symlink() and child.name not in SKIP_DIRS:
                    stack.append(child)
                elif child.is_file():
                    stack.append(child)
            continue
        if current.is_file() and (current.suffix.lower() in TEXT_GLOBS or current.name.lower() == "skill.md"):
            files.append(current)
    return sorted(files)


def scan_text(text: str, path: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path)
    for i, line in enumerate(text.splitlines(), 1):
        if IGNORE_RE.search(line):
            findings.append(Finding("danger", "prompt-override", "instructions tell the agent to ignore previous or system prompts", rel, i))
        if EXFIL_HOST_RE.search(line):
            findings.append(Finding("danger", "exfil-host", "mentions a common exfiltration or webhook host", rel, i))
        if CURL_RE.search(line) and (SECRET_PATH_RE.search(line) or ENV_SECRET_RE.search(line) or EXFIL_HOST_RE.search(line)):
            findings.append(Finding("danger", "secret-outbound", "outbound request appears to involve a secret or an exfil host", rel, i))
        elif CURL_RE.search(line):
            findings.append(Finding("warning", "outbound-request", "script or instructions make an outbound HTTP request", rel, i))
        if SECRET_PATH_RE.search(line) and re.search(r"\b(cat|type|read|open|print|echo|Get-Content)\b", line, re.I):
            findings.append(Finding("danger", "secret-read", "reads a credential or secret file", rel, i))
        if SMUGGLE_RE.search(line):
            findings.append(Finding("danger", "log-smuggle", "asks the agent to forward a conversation or to run a side call after every tool", rel, i))
        if BASE64_RUN_RE.search(line) and (LONG_B64_RE.search(line) or "base64" in line.lower()):
            findings.append(Finding("warning", "obfuscated-exec", "decode-and-run or eval pattern", rel, i))
        if LONG_B64_RE.search(line):
            findings.append(Finding("warning", "long-base64", "long base64 blob; inspect before running", rel, i))
        for ch, label in HIDDEN_CHARS.items():
            if ch in line:
                findings.append(Finding("danger", "hidden-unicode", f"contains hidden unicode ({label})", rel, i))
                break
    return findings


def read_text_capped(path: Path) -> tuple[str | None, Finding | None]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, Finding("warning", "unreadable-file", f"could not read file: {exc}", str(path), None)
    if size > MAX_FILE_BYTES:
        return None, Finding(
            "warning",
            "file-too-large",
            f"skipped file larger than {MAX_FILE_BYTES} bytes",
            str(path),
            None,
        )
    return path.read_text(encoding="utf-8", errors="replace"), None


def scan_root(root: Path) -> list[Finding]:
    root = root.resolve()
    if root.is_file():
        text, skipped = read_text_capped(root)
        if skipped:
            return [skipped]
        return scan_text(text or "", root)
    findings: list[Finding] = []
    text_files = iter_text_files(root)
    skill_md = [p for p in text_files if p.name.lower() == "skill.md"]
    if not skill_md:
        findings.append(Finding("warning", "no-skill-md", "no SKILL.md found in this folder", str(root), None))
    for path in text_files:
        text, skipped = read_text_capped(path)
        if skipped:
            findings.append(skipped)
            continue
        findings.extend(scan_text(text or "", path))
    return findings


def verdict(findings: list[Finding]) -> str:
    if any(f.severity == "danger" for f in findings):
        return "DANGER"
    if any(f.severity == "warning" for f in findings):
        return "WARN"
    return "SAFE"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="skillguard",
        description="Static, no-LLM security scanner for untrusted agent skills.",
    )
    parser.add_argument("path", help="skill folder or file")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--version", action="version", version=f"skillguard {__version__}")
    args = parser.parse_args(argv)
    target = Path(args.path)
    if not target.exists():
        print(f"error: path not found: {target}", file=sys.stderr)
        return 1
    findings = scan_root(target)
    result = verdict(findings)
    if args.json:
        print(json.dumps({"verdict": result, "findings": [asdict(f) for f in findings]}, indent=2))
    else:
        print(f"verdict: {result}")
        if not findings:
            print("no findings")
        for f in findings:
            loc = f"{f.path}:{f.line}" if f.line else f.path
            print(f"{f.severity}: {f.code}: {f.message} ({loc})")
    if result == "DANGER":
        return 2
    if result == "WARN":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
