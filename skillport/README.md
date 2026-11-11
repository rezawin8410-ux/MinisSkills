# skillport

Write one agent skill folder, install it into Claude Code, Claude Fable 5.2,
Grok, Cursor, Codex, and GitHub Copilot paths, and catch copies that have
drifted apart.

Same-skill-in-three-tools copy drift is a complaint that showed up on X in
early November 2026. skillport is an original, clean-room tool for that job.
It is not a fork of the Vercel Skills CLI or of any skill manager.

## Install

Python 3.10 or later.

From a checkout:

```bash
python3 -m pip install .
python3 -m pip install -e ".[dev]"  # optional, for tests
skillport --help
```

Or without installing:

```bash
PYTHONPATH=. python3 -m skillport --help
```

## 30-second quick start

```bash
mkdir -p my-skill
cat > my-skill/SKILL.md << 'EOF'
---
name: my-skill
description: Demo skill used when you want a single portable SKILL.md.
---

Remind the user to keep one canonical copy of this skill.
EOF

skillport install ./my-skill --root . --agents claude,fable,grok,cursor
skillport status --root . --agents claude,fable,grok,cursor
```

Real output from that skill text (9 Nov 2026, America/Los_Angeles). Claude and
Fable share one folder, so they are installed once and both names are listed:

```
installed my-skill -> ./.claude/skills/my-skill (copy; agents: claude, fable)
installed my-skill -> ./.grok/skills/my-skill (copy)
installed my-skill -> ./.cursor/skills/my-skill (copy)
agent	name	kind	hash	path
claude	my-skill	copy	47df1b91ec8f	./.claude/skills/my-skill
fable	my-skill	copy	47df1b91ec8f	./.claude/skills/my-skill
grok	my-skill	copy	47df1b91ec8f	./.grok/skills/my-skill
cursor	my-skill	copy	47df1b91ec8f	./.cursor/skills/my-skill
no drift
```

`install --link` puts a symlink at each path so one edit updates every agent.
`status` exits `2` if two copies of the same skill name have different content.
`sync` overwrites those copies from a canonical folder.

`--target fable` or `--target grok` is an agent name, not a folder named
`fable`. `--target ./somewhere` (or any other path) still writes to that
directory.

Default project paths:

| Agent | Path | Notes |
|---|---|---|
| claude | `.claude/skills` | Claude Code / Agent Skills |
| fable | `.claude/skills` | Same as claude. Claude Fable 5.2 uses the standard SKILL.md layout, not a separate format. |
| agents | `.agents/skills` | Tool-agnostic Agent Skills folder |
| copilot | `.github/skills` | No default user-level path |
| cursor | `.cursor/skills` | |
| codex | `.codex/skills` | |
| grok | `.grok/skills` | Documented for Grok Build. See the limits below for Grok Bot. |

`--global` uses the matching folder under your home directory (Copilot has no
default user path). `--target DIR` writes to a single folder instead.

## Use it with your agent

You can point a coding agent at this repository and ask it to install a skill,
check drift, or write a similar small CLI in the same style.

Sample prompt:

```
Use the skillport tool in this repo. Install it in a fresh virtualenv with
`python3 -m pip install .`, run `python3 -m pytest -q`, then install the
sample skill from the README into a temporary project with
`--agents claude,fable,grok`. Summarize where the files landed and why Claude
and Fable share a folder. Do not publish the package, change repository
visibility, or copy third-party source. If you want to build a similar tool,
match this repo's layout: a small MIT Python CLI, behavior tests, honest
limits in the README, and no secrets.
```

## How you can help

- Send a minimal skill folder that installed to the wrong place, or a `status`
  report that missed real drift or flagged identical copies.
- Add a test for a path convention we should support, with a public doc link.
- Improve the README if a default path has changed upstream.
- Fix typos and clarify the Grok Bot vs Grok Build note if you have a
  verified official path.

See `CONTRIBUTING.md` for how to run tests and propose a change. Please do
not paste secrets into issues.

## Credit

Inspired by public reports of cross-agent skill drift and by GitHub's documented
Copilot skill paths (`.claude/skills`, `.github/skills`, `.agents/skills`).
Fable paths follow Anthropic's public Agent Skills / Claude Code layout
(`.claude/skills`, `~/.claude/skills`). Grok Build paths follow
[xAI's skills docs](https://docs.x.ai/build/features/skills-plugins-marketplaces)
(`.grok/skills`, `~/.grok/skills`). No source was copied from those tools or
posts.

## Limits

- Default paths follow public docs and common conventions; they may lag a vendor change. Use `--target` to override.
- `fable` is an alias for the Claude / Agent Skills directories. It does not rewrite a skill to be "Fable-tuned."
- `grok` installs to `.grok/skills` (and `~/.grok/skills` with `--global`). That path is documented for Grok Build. Grok Bot (the chat product) documents saving private skills in the product, not a single official repo-root drop path we could verify. On some Grok Bot boxes, skills also live under `agent-data/workflows`. Use `--target` for that or any other folder.
- `status` detects drift by hashing file contents. It does not watch for live edits.
- Skill names with path separators (`/` or `\`) are refused so an untrusted frontmatter `name` cannot write outside the destination directory.
- `--global` uses folders under your home directory. Copilot has no default user-level path.

From GitHub you can also install with:

```bash
python3 -m pip install git+https://github.com/perron/skillport.git
```

This package is not published to PyPI.

## License

MIT. See `LICENSE`. Please read `SECURITY.md` before filing a vulnerability report.
