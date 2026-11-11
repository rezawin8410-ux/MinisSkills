#!/usr/bin/env python3
"""Install aswath-capability's subagent briefs as real Claude Code agent types.

Claude Code discovers custom agents in ~/.claude/agents/. The canonical copies
live inside the skill so the skill stays self-contained and portable; this
script installs them where they can actually be spawned via the Agent tool.

Usage:
    python install-agents.py            # install
    python install-agents.py --uninstall # remove
    python install-agents.py --check    # report status, change nothing
"""

import argparse
import shutil
import sys
from pathlib import Path

AGENT_NAMES = ["aswath-researcher", "aswath-planner", "aswath-critic"]
MARKER = "aswath-capability"


def skill_root(home=None):
    home = Path(home) if home else Path.home()
    for base in (home / ".agents" / "skills", home / ".claude" / "skills"):
        p = base / "aswath-capability"
        if p.is_dir():
            return p
    raise SystemExit("aswath-capability skill not found")


def agents_dir(home=None):
    home = Path(home) if home else Path.home()
    return home / ".claude" / "agents"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=None)
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    root = skill_root(args.home)
    src = root / "agents"
    dest = agents_dir(args.home)

    if args.check:
        for n in AGENT_NAMES:
            installed = (dest / f"{n}.md").is_file()
            canonical = (src / f"{n}.md").is_file()
            print(f"  {n:<22} canonical={'yes' if canonical else 'NO'}  installed={'yes' if installed else 'no'}")
        return 0

    if args.uninstall:
        for n in AGENT_NAMES:
            p = dest / f"{n}.md"
            if p.is_file():
                p.unlink()
                print(f"removed {p}")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    for n in AGENT_NAMES:
        s = src / f"{n}.md"
        if not s.is_file():
            print(f"missing canonical agent: {s}")
            continue
        shutil.copy2(s, dest / f"{n}.md")
        print(f"installed {dest / f'{n}.md'}")
    print(f"\n{n} agent type(s) now available to the Agent tool.".replace("{n}", str(len(AGENT_NAMES))))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
