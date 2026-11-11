"""Install one skill folder into several coding-agent skill paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Iterable

__version__ = "0.1.2"

# Default install locations relative to a project root, and optional home paths.
# Project-relative paths are used unless --global is set (home paths only).
# These follow public docs and widely reported conventions; override with --target.
# fable shares Claude's directories because Claude Fable 5.2 uses the standard
# Agent Skills / Claude Code skill layout (.claude/skills), not a separate format.
# grok uses the documented Grok Build paths (.grok/skills). Grok Bot (the chat
# product) does not publish one official repo-root skill path that we could
# verify; pass --target for a custom folder.
AGENT_PATHS = {
    "claude": {"project": ".claude/skills", "home": ".claude/skills"},
    "fable": {"project": ".claude/skills", "home": ".claude/skills"},
    "agents": {"project": ".agents/skills", "home": ".agents/skills"},
    "copilot": {"project": ".github/skills", "home": None},
    "cursor": {"project": ".cursor/skills", "home": ".cursor/skills"},
    "codex": {"project": ".codex/skills", "home": ".codex/skills"},
    "grok": {"project": ".grok/skills", "home": ".grok/skills"},
}

DEFAULT_AGENTS = ("claude", "fable", "agents", "copilot", "cursor", "codex", "grok")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    data: dict = {}
    for raw in parts[1].splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def validate_install_name(name: str) -> str:
    cleaned = (name or "").strip()
    if (
        not cleaned
        or cleaned in {".", ".."}
        or "/" in cleaned
        or "\\" in cleaned
        or "\0" in cleaned
    ):
        raise ValueError(f"invalid skill name {name!r}: must be a single path segment")
    return cleaned


def skill_name(skill_dir: Path) -> str:
    skill_md = find_skill_md(skill_dir)
    if skill_md:
        name = parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace")).get("name")
        if name:
            return validate_install_name(name)
    return validate_install_name(skill_dir.name)


def find_skill_md(skill_dir: Path) -> Path | None:
    exact = skill_dir / "SKILL.md"
    if exact.is_file():
        return exact
    for path in skill_dir.iterdir():
        if path.is_file() and path.name.lower() == "skill.md":
            return path
    return None


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def tree_hash(skill_dir: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(p for p in skill_dir.rglob("*") if p.is_file())
    for path in files:
        rel = path.relative_to(skill_dir).as_posix()
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def resolve_target(agent: str, root: Path, global_install: bool) -> Path:
    if agent not in AGENT_PATHS:
        raise ValueError(f"unknown agent: {agent}")
    spec = AGENT_PATHS[agent]
    if global_install:
        home_rel = spec["home"]
        if not home_rel:
            raise ValueError(f"{agent} has no default user-level path; pass --target")
        return Path.home() / home_rel
    return root / spec["project"]


def is_agent_name(value: str) -> bool:
    """True when value is a known agent key, not a filesystem path."""
    if not value or value.startswith(".") or "/" in value or "\\" in value:
        return False
    return value in AGENT_PATHS


def grouped_destinations(
    agents: Iterable[str], root: Path, global_install: bool
) -> list[tuple[tuple[str, ...], Path]]:
    """Group agents that share the same destination folder (e.g. claude and fable)."""
    order: list[str] = []
    groups: dict[str, list[str]] = {}
    paths: dict[str, Path] = {}
    for agent in agents:
        parent = resolve_target(agent, root, global_install)
        key = str(parent)
        if key not in groups:
            groups[key] = []
            paths[key] = parent
            order.append(key)
        groups[key].append(agent)
    return [(tuple(groups[k]), paths[k]) for k in order]


def install_one(
    skill_dir: Path,
    dest_parent: Path,
    *,
    link: bool,
    name: str | None = None,
) -> Path:
    skill_dir = skill_dir.resolve()
    if not skill_dir.is_dir():
        raise FileNotFoundError(f"skill directory not found: {skill_dir}")
    if find_skill_md(skill_dir) is None:
        raise FileNotFoundError(f"no SKILL.md in {skill_dir}")
    dest_parent.mkdir(parents=True, exist_ok=True)
    dest = dest_parent / validate_install_name(name or skill_name(skill_dir))
    if dest.exists() or dest.is_symlink():
        if dest.is_dir() and not dest.is_symlink():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    if link:
        dest.symlink_to(skill_dir, target_is_directory=True)
    else:
        shutil.copytree(skill_dir, dest)
    return dest


def iter_installed(root: Path, agents: Iterable[str], global_install: bool) -> list[dict]:
    rows = []
    for agent in agents:
        try:
            parent = resolve_target(agent, root, global_install)
        except ValueError:
            continue
        if not parent.is_dir():
            continue
        for child in sorted(parent.iterdir()):
            if not child.is_dir() and not child.is_symlink():
                continue
            skill_md = find_skill_md(child)
            rows.append(
                {
                    "agent": agent,
                    "name": child.name,
                    "path": str(child),
                    "link": child.is_symlink(),
                    "link_target": str(child.resolve()) if child.is_symlink() else None,
                    "hash": tree_hash(child.resolve()) if (child.is_dir() or child.is_symlink()) and skill_md else None,
                    "skill_md": skill_md.name if skill_md else None,
                }
            )
    return rows


def drift_groups(rows: list[dict]) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for row in rows:
        if not row.get("hash"):
            continue
        groups.setdefault(row["name"], set()).add(row["hash"])
    return {name: hashes for name, hashes in groups.items() if len(hashes) > 1}


def cmd_agents(_: argparse.Namespace) -> int:
    print("agent\tproject path\tuser path")
    for agent, spec in AGENT_PATHS.items():
        home = f"~/{spec['home']}" if spec["home"] else "(none)"
        print(f"{agent}\t{spec['project']}\t{home}")
    return 0


def _print_installed(name: str, dest: Path, agents: tuple[str, ...] | None = None) -> None:
    kind = "link" if dest.is_symlink() else "copy"
    suffix = f"; agents: {', '.join(agents)}" if agents and len(agents) > 1 else ""
    print(f"installed {name} -> {dest} ({kind}{suffix})")


def cmd_install(args: argparse.Namespace) -> int:
    skill_dir = Path(args.skill_dir)
    root = Path(args.root).resolve()
    name = skill_name(skill_dir)
    dests: list[tuple[Path, tuple[str, ...] | None]] = []
    if args.target:
        if is_agent_name(args.target):
            parent = resolve_target(args.target, root, args.global_install)
            dests.append((install_one(skill_dir, parent, link=args.link, name=name), (args.target,)))
        else:
            dests.append((install_one(skill_dir, Path(args.target), link=args.link, name=name), None))
    else:
        agents = _split_agents(args.agents)
        for group, parent in grouped_destinations(agents, root, args.global_install):
            dests.append((install_one(skill_dir, parent, link=args.link, name=name), group))
    for dest, group in dests:
        _print_installed(name, dest, group)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    agents = _split_agents(args.agents)
    rows = iter_installed(root, agents, args.global_install)
    if args.json:
        print(json.dumps({"skills": rows, "drift": list(drift_groups(rows))}, indent=2))
        return 0
    if not rows:
        print("no skills installed in the requested paths")
        return 0
    print("agent\tname\tkind\thash\tpath")
    for row in rows:
        kind = "link" if row["link"] else "copy"
        short = (row["hash"] or "-")[:12]
        print(f"{row['agent']}\t{row['name']}\t{kind}\t{short}\t{row['path']}")
    drifted = drift_groups(rows)
    if drifted:
        print("drift detected:")
        for name in sorted(drifted):
            print(f"  {name} has {len(drifted[name])} different copies")
        return 2
    print("no drift")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    skill_dir = Path(args.skill_dir)
    root = Path(args.root).resolve()
    agents = _split_agents(args.agents)
    name = skill_name(skill_dir)
    for group, parent in grouped_destinations(agents, root, args.global_install):
        dest = install_one(skill_dir, parent, link=args.link, name=name)
        extra = f" ({', '.join(group)})" if len(group) > 1 else ""
        print(f"synced {name} -> {dest}{extra}")
    return 0


def _split_agents(value: str | None) -> list[str]:
    if not value:
        return list(DEFAULT_AGENTS)
    agents = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [a for a in agents if a not in AGENT_PATHS]
    if unknown:
        raise SystemExit(f"unknown agents: {', '.join(unknown)}")
    return agents


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skillport",
        description="Install one skill folder into several coding-agent skill paths and catch copy drift.",
    )
    parser.add_argument("--version", action="version", version=f"skillport {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_agents = sub.add_parser("agents", help="list default agent skill paths")
    p_agents.set_defaults(func=cmd_agents)

    p_install = sub.add_parser("install", help="copy or symlink a skill into agent paths")
    p_install.add_argument("skill_dir")
    p_install.add_argument("--root", default=".", help="project root (default: current directory)")
    p_install.add_argument("--agents", help="comma-separated agents (default: all)")
    p_install.add_argument(
        "--target",
        help="known agent name (fable, grok, claude, ...) or a directory to install into instead of the agent map",
    )
    p_install.add_argument("--link", action="store_true", help="symlink instead of copy")
    p_install.add_argument("--global", dest="global_install", action="store_true", help="use user-level paths")
    p_install.set_defaults(func=cmd_install)

    p_status = sub.add_parser("status", help="list installed skills and report copy drift")
    p_status.add_argument("--root", default=".")
    p_status.add_argument("--agents")
    p_status.add_argument("--global", dest="global_install", action="store_true")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

    p_sync = sub.add_parser("sync", help="overwrite installed copies from a canonical skill folder")
    p_sync.add_argument("skill_dir")
    p_sync.add_argument("--root", default=".")
    p_sync.add_argument("--agents")
    p_sync.add_argument("--link", action="store_true")
    p_sync.add_argument("--global", dest="global_install", action="store_true")
    p_sync.set_defaults(func=cmd_sync)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
