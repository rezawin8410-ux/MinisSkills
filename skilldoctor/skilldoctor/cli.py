"""Lint Agent Skills against the public Agent Skills specification."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from difflib import get_close_matches
from pathlib import Path

__version__ = "0.1.1"

# Official frontmatter keys from https://agentskills.io/specification
# as fetched on 9 Nov 2026. hyphenated allowed-tools is the published name.
OFFICIAL_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
REQUIRED_KEYS = {"name", "description"}
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VAGUE_ONLY = re.compile(
    r"^(helps? with|a skill for|utility for|used for|writing|helper|misc|stuff)\b",
    re.I,
)
WHEN_RE = re.compile(r"\b(when|use when|whenever|if the user|for when)\b", re.I)

# Keys seen in the wild that the published spec does not define, plus
# underscore/camel typo forms of official keys.
TYPO_HINTS = {
    "allowed_tools": "allowed-tools",
    "allowedtools": "allowed-tools",
    "allowedTools": "allowed-tools",
    "disable_model_invocation": None,
    "disable-model-invocation": None,
    "when_to_use": "description (say when to use the skill there)",
    "when-to-use": "description (say when to use the skill there)",
    "Name": "name",
    "Description": "description",
}


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


@dataclass
class Finding:
    severity: str  # error, warning, info
    code: str
    message: str
    path: str


def split_frontmatter(text: str) -> tuple[str | None, str]:
    if not text.startswith("---"):
        return None, text
    rest = text[3:]
    if rest.startswith("\n"):
        rest = rest[1:]
    end = rest.find("\n---")
    if end < 0:
        return None, text
    return rest[:end], rest[end + 4 :].lstrip("\n")


def parse_simple_yaml_map(block: str) -> tuple[dict, list[str]]:
    """Parse a flat YAML map plus one level of nested `metadata:` keys.

    This is not a full YAML parser. It covers the official skill frontmatter
    shape (string fields and a string-to-string metadata map).
    """
    data: dict = {}
    errors: list[str] = []
    current_map: dict | None = None
    current_key: str | None = None
    for raw in block.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        if raw.startswith("  ") or raw.startswith("\t"):
            if current_map is None:
                errors.append(f"unexpected indented line: {raw.strip()}")
                continue
            if ":" not in raw:
                errors.append(f"invalid metadata line: {raw.strip()}")
                continue
            k, v = raw.strip().split(":", 1)
            current_map[k.strip()] = v.strip().strip('"').strip("'")
            continue
        current_map = None
        if ":" not in raw:
            errors.append(f"invalid line: {raw.strip()}")
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value == "" or value in ("|", ">"):
            current_map = {}
            data[key] = current_map
            current_key = key
            continue
        data[key] = value.strip('"').strip("'")
        current_key = key
    return data, errors


def lint_text(text: str, path: Path, parent_dir: Path | None = None) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path)

    if path.name != "SKILL.md":
        findings.append(Finding(
            "error",
            "filename-case",
            f"skill file must be named exactly SKILL.md (found {path.name}). "
            "On Linux a different case will not load.",
            rel,
        ))

    fm, body = split_frontmatter(text)
    if fm is None:
        findings.append(Finding("error", "frontmatter-missing", "SKILL.md must start with YAML frontmatter delimited by ---.", rel))
        return findings

    data, parse_errors = parse_simple_yaml_map(fm)
    for err in parse_errors:
        findings.append(Finding("error", "frontmatter-parse", err, rel))

    for key in REQUIRED_KEYS:
        if key not in data or (isinstance(data[key], str) and not str(data[key]).strip()):
            findings.append(Finding("error", "missing-field", f"missing required field: {key}", rel))

    for key in data:
        if key not in OFFICIAL_KEYS:
            hint = TYPO_HINTS.get(key)
            if hint is None and key in TYPO_HINTS:
                findings.append(Finding(
                    "error",
                    "unknown-field",
                    f"field {key!r} is not in the Agent Skills spec and will be ignored",
                    rel,
                ))
            elif hint:
                findings.append(Finding(
                    "error",
                    "typo-field",
                    f"field {key!r} is not in the Agent Skills spec; did you mean {hint}?",
                    rel,
                ))
            else:
                close = get_close_matches(key, list(OFFICIAL_KEYS), n=1, cutoff=0.6)
                extra = f"; did you mean {close[0]}?" if close else ""
                findings.append(Finding(
                    "warning",
                    "unknown-field",
                    f"field {key!r} is not in the Agent Skills spec{extra}",
                    rel,
                ))

    name = data.get("name")
    if isinstance(name, str):
        if not NAME_RE.match(name) or len(name) > 64:
            findings.append(Finding(
                "error",
                "invalid-name",
                "name must be 1-64 characters, lowercase letters, numbers and single hyphens, "
                "and must not start or end with a hyphen",
                rel,
            ))
        expected_dir = parent_dir.name if parent_dir is not None else path.parent.name
        if expected_dir and expected_dir != name:
            findings.append(Finding(
                "error",
                "name-mismatch",
                f"name {name!r} must match the parent directory name {expected_dir!r}",
                rel,
            ))

    desc = data.get("description")
    if isinstance(desc, str):
        if len(desc) > 1024:
            findings.append(Finding("error", "description-length", "description must be at most 1024 characters", rel))
        elif len(desc) < 40 or VAGUE_ONLY.search(desc.strip()):
            findings.append(Finding(
                "warning",
                "weak-description",
                "description is vague or short; agents pick skills from this field and a line "
                "like 'Helps with writing.' often never triggers. Say what it does and when to use it.",
                rel,
            ))
        elif not WHEN_RE.search(desc):
            findings.append(Finding(
                "warning",
                "no-when",
                "description does not say when to use the skill; the spec asks for both what and when",
                rel,
            ))

    compat = data.get("compatibility")
    if isinstance(compat, str) and len(compat) > 500:
        findings.append(Finding("error", "compatibility-length", "compatibility must be at most 500 characters", rel))

    allowed = data.get("allowed-tools")
    if allowed is not None and not isinstance(allowed, str):
        findings.append(Finding("error", "allowed-tools-type", "allowed-tools must be a space-separated string", rel))

    metadata = data.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        findings.append(Finding("error", "metadata-type", "metadata must be a mapping of strings", rel))

    lines = body.splitlines()
    if len(lines) > 500:
        findings.append(Finding(
            "warning",
            "body-long",
            f"SKILL.md body is {len(lines)} lines; the spec recommends keeping it under 500 and moving detail into references/",
            rel,
        ))
    tokens = estimate_tokens(body)
    if tokens > 5000:
        findings.append(Finding(
            "warning",
            "body-tokens",
            f"SKILL.md body is about {tokens} tokens (4-char estimate); the spec recommends under 5000 once loaded",
            rel,
        ))
    return findings


def lint_path(path: Path) -> list[Finding]:
    path = path.resolve()
    if path.is_dir():
        matches = list(path.rglob("*"))
        skill_files = [p for p in matches if p.is_file() and p.name.lower() == "skill.md"]
        if not skill_files:
            return [Finding("error", "not-found", f"no SKILL.md under {path}", str(path))]
        findings: list[Finding] = []
        for skill in sorted(skill_files):
            findings.extend(lint_text(skill.read_text(encoding="utf-8", errors="replace"), skill, skill.parent))
        return findings
    text = path.read_text(encoding="utf-8", errors="replace")
    return lint_text(text, path, path.parent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="skilldoctor",
        description="Lint Agent Skills (SKILL.md) for spec errors, typo keys, and weak descriptions.",
    )
    parser.add_argument("path", help="SKILL.md file or a skill folder (or a tree of them)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--version", action="version", version=f"skilldoctor {__version__}")
    args = parser.parse_args(argv)
    target = Path(args.path)
    if not target.exists():
        print(f"error: path not found: {target}", file=sys.stderr)
        return 1
    findings = lint_path(target)
    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")
    if args.json:
        print(json.dumps({"findings": [asdict(f) for f in findings], "errors": errors, "warnings": warnings}, indent=2))
    else:
        if not findings:
            print(f"ok: {target} is clean")
        else:
            for f in findings:
                print(f"{f.severity}: {f.code}: {f.message} ({f.path})")
            print(f"{errors} error(s), {warnings} warning(s)")
    if errors:
        return 2
    if warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
