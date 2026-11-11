#!/usr/bin/env python3
"""Stage a marketplace plugin for the user to approve before installing.

This script never installs anything. It searches the marketplace catalog,
writes a staging file describing the candidate, and prints the exact command
the user can run if they approve. Approval is the user's call, and a plugin
can ship hooks and MCP servers that run outside the skill system.

Usage:
    python stage-install.py --search "ecommerce cart checkout"
    python stage-install.py --stage "Awesome Ecom Skills"
    python stage-install.py --staged
"""

import argparse
import json
import subprocess
import os
import sys
import time
from pathlib import Path

STAGING_NAME = "staged.json"


# --- shared router state -------------------------------------------------
# The router is installed in more than one skills root. The mutable state
# - the registry index and the outcome ledger - must have exactly one copy,
# or the two installs drift and the router ranks differently depending on
# who is asking. Shared location: ~/.minimax/local-runtime/skill-learning/
def shared_state_dir():
    """Where the mutable router state lives.

    Overridable so the router can run from a checkout, a container or a
    CI job instead of one machine's home directory.
    """
    env = os.environ.get("SKILL_ROUTER_STATE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".minimax" / "local-runtime" / "skill-learning"


def shared_registry_path():
    return shared_state_dir() / 'skills.json'


def shared_ledger_path():
    return shared_state_dir() / 'ledger.json'


def slugify(name):
    import re
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").lower())
    return s.strip("-")


def skill_root(home=None):
    home = Path(home) if home else Path.home()
    for base in (home / ".agents" / "skills", home / ".claude" / "skills"):
        p = base / "aswath-capability"
        if p.is_dir():
            return p
    raise SystemExit("aswath-capability skill not found")


def registry_path(root):
    return shared_registry_path()


def staging_path(root):
    return root / "registry" / STAGING_NAME


def load_registry(root):
    p = registry_path(root)
    if not p.is_file():
        raise SystemExit("registry not found - run scripts/build-registry.py first")
    return json.loads(p.read_text(encoding="utf-8"))


def cmd_search(args, root):
    reg = load_registry(root)
    words = [w.lower() for w in args.search.split() if len(w) > 2]
    hits = []
    for p in reg.get("installable", []):
        hay = f"{p.get('plugin','')} {p.get('summary','')}".lower()
        score = sum(1 for w in words if w in hay)
        if score:
            hits.append((score, p))
    hits.sort(key=lambda t: -t[0])
    if not hits:
        print(f"no marketplace plugin matches: {args.search}")
        return 1
    print(f"== marketplace candidates for: {args.search} ({len(hits)}) ==\n")
    for score, p in hits[:args.limit]:
        ships = reg.get("marketplace", [])
        slug = p.get("slug") or slugify(p.get("plugin"))
        n_skills = sum(1 for s in ships if (s.get("plugin") or "").split("@")[0] == slug)
        print(f"  {p.get('plugin')}  v{p.get('version')}")
        print(f"      {(p.get('summary') or '')[:160]}")
        print(f"      repo: {p.get('repo')}" + (f"  path: {p.get('repo_path')}" if p.get("repo_path") else ""))
        print(f"      reviewed_by: {p.get('reviewed_by')}   skills shipped: {n_skills}")
        print()
    print("To stage one for approval:")
    print(f"   python stage-install.py --stage \"<plugin name>\"")
    return 0


def cmd_stage(args, root):
    reg = load_registry(root)
    target = None
    for p in reg.get("installable", []):
        if p.get("plugin", "").lower() == args.stage.lower():
            target = p
            break
    if not target:
        print(f"not found in marketplace catalog: {args.plugin}")
        return 1

    # What does this plugin actually ship? Hooks and MCP servers are the risk.
    slug = target.get("slug") or slugify(target.get("plugin"))
    shipped = [s for s in reg.get("marketplace", [])
               if (s.get("plugin") or "").split("@")[0] == slug]
    kinds = {}
    for s in shipped:
        kinds[s.get("kind") or "skill"] = kinds.get(s.get("kind") or "skill", 0) + 1

    entry = {
        "staged_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "plugin": target.get("plugin"),
        "version": target.get("version"),
        "summary": target.get("summary"),
        "repo": target.get("repo"),
        "repo_path": target.get("repo_path"),
        "reviewed_by": target.get("reviewed_by"),
        "ships": kinds,
        "skills": sorted(s.get("name") for s in shipped if s.get("kind") is None),
        "install_command": (
            f"/plugin install {target.get('plugin')}@claude-plugins-official"
        ),
        "status": "awaiting-approval",
    }

    p = staging_path(root)
    data = {"staged": []}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"staged": []}
    data.setdefault("staged", []).append(entry)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"STAGED: {target.get('plugin')} v{target.get('version')}")
    print(f"  summary : {(target.get('summary') or '')[:200]}")
    print(f"  repo    : {target.get('repo')}")
    print(f"  ships   : {kinds}")
    if kinds.get("mcp") or kinds.get("hook"):
        print("  WARNING: this plugin ships MCP servers or hooks. These run outside")
        print("           the skill system and cannot be audited at call time.")
    elif not kinds:
        print("  NOTE: this plugin's contents are not in the local catalog, so its")
        print("        hooks, MCP servers and skills are unknown. Read the repo first.")
    print(f"\n  install with: {entry['install_command']}")
    print(f"  staging file: {p}")
    print("\n  Nothing has been installed. Show this to the user and wait for a yes.")
    return 0


def cmd_staged(args, root):
    p = staging_path(root)
    if not p.is_file():
        print("nothing staged")
        return 0
    data = json.loads(p.read_text(encoding="utf-8"))
    for e in data.get("staged", []):
        print(f"  [{e.get('status')}] {e.get('plugin')} v{e.get('version')}  "
              f"ships={e.get('ships')}")
        print(f"      {e.get('install_command')}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=None)
    ap.add_argument("--search")
    ap.add_argument("--stage")
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()
    root = skill_root(args.home)
    if args.staged:
        return cmd_staged(args, root)
    if args.stage:
        return cmd_stage(args, root)
    if args.search:
        return cmd_search(args, root)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
