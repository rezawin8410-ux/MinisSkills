#!/usr/bin/env python3
"""setup-clone: run once after cloning a notes repository. Git does not copy hook settings with a clone, so this activates the
versioned hooks (secret refusal before every commit) and checks that the tools can run."""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import _myco  # noqa: E402


def main():
    _myco.utf8_console()
    root = _myco.toplevel(HERE)
    if not root:
        print("setup-clone: not inside a git repository", file=sys.stderr)
        return 1
    lay = _myco.layout(root)
    tools = lay["tools"]
    _myco.git("-C", root, "config", "core.hooksPath", tools + "/hooks")
    if not _myco.IS_WINDOWS:
        for d in (tools, tools + "/hooks"):
            full = os.path.join(root, *d.split("/"))
            for f in os.listdir(full):
                p = os.path.join(full, f)
                if os.path.isfile(p):
                    try:
                        os.chmod(p, os.stat(p).st_mode | 0o111)
                    except OSError:
                        pass
    name = _myco.git("-C", root, "config", "user.name").stdout.strip()
    email = _myco.git("-C", root, "config", "user.email").stdout.strip()
    if not (name and email):
        print('setup-clone: set your identity first: git config user.name "Your Name"; git config user.email you@example.com', file=sys.stderr)
    if not shutil.which("gh"):
        print("note: gh (GitHub CLI) not found; 'notes-commit --pr' will print the branch instead of opening a pull request")
    print(f'setup-clone: ready (hooks: {tools}/hooks). Next: read AGENTS.md, then work on a branch:  {tools}/notes-commit --branch my-change --pr "what changed and why"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
