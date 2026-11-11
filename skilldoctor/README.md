# skilldoctor

Lint an Agent Skill (`SKILL.md`) against the public
[Agent Skills specification](https://agentskills.io/specification). Catch the
failures that otherwise go silent: a typo in frontmatter, a file named
`skill.md` on Linux, a directory name that does not match `name`, or a
description so vague that the agent never triggers the skill.

## Install

Python 3.10 or later.

From a checkout:

```bash
python3 -m pip install .
python3 -m pip install -e ".[dev]"  # optional, for tests
skilldoctor path/to/skill
```

Or without installing:

```bash
PYTHONPATH=. python3 -m skilldoctor path/to/skill
```

## 30-second quick start

```bash
mkdir -p tidy-notes
cat > tidy-notes/SKILL.md << 'EOF'
---
name: tidy-notes
description: Helps with writing.
allowed_tools: Bash
---

Tidy the notes.
EOF

skilldoctor ./tidy-notes
```

Sample output from that exact example:

```
$ skilldoctor ./tidy-notes
error: typo-field: field 'allowed_tools' is not in the Agent Skills spec; did you mean allowed-tools? (/tmp/qs/skilldoctor/tidy-notes/SKILL.md)
warning: weak-description: description is vague or short; agents pick skills from this field and a line like 'Helps with writing.' often never triggers. Say what it does and when to use it. (/tmp/qs/skilldoctor/tidy-notes/SKILL.md)
1 error(s), 1 warning(s)
exit: 2
```

This run used `/tmp/qs/skilldoctor`. The file path in the findings will match your working directory.

You should see an error that `allowed_tools` should be `allowed-tools`, a
warning that the description is too vague to trigger, and a non-zero exit.

Exit codes: `0` clean, `1` warnings only, `2` errors.

## Credit

Field names and constraints follow the public Agent Skills specification
(fetched 9 Nov 2026). The typo and trigger-quality checks were motivated by
public reports of silent frontmatter failures and of vague descriptions that
never loaded. No linter source was copied.

## Limits

- The frontmatter parser is intentionally small: a flat YAML map plus one level of nested `metadata:` keys. It is not a full YAML parser.
- Field names and length limits follow the public Agent Skills specification as fetched on 9 Nov 2026. The spec may change.
- A clean report means the file matches those checks, not that the skill is safe to run. Use skillguard for a static security pass.

From GitHub you can also install with:

```bash
python3 -m pip install git+https://github.com/perron/skilldoctor.git
```

This package is not published to PyPI.

## Use it with your agent

You can point a coding agent at this repository and ask it to use the tool, adapt it, or write a small tool in the same style. That works with Claude Code, Codex, Cursor, Grok Bot, and anything else that can clone a git repo and run a Python CLI.

A short prompt you can paste:

> Clone https://github.com/perron/skilldoctor, install it, and lint every SKILL.md in this repo. Fix the errors it reports, and leave warnings for me to review.

If you want a new tool in this style, say so explicitly: one job, standard-library Python, pytest first, no API key, and an honest Limits section.

## Contributing / how you can help

Bug reports, tight tests, and small honest features are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to run the suite and for pull request guidelines.

Good first issues:

- When the Agent Skills spec adds a field, add it to the official key list with a test.
- Support a folded/multi-line YAML `description` without inventing a full YAML parser.
- Emit a machine-readable code list (for editor integrations) in addition to `--json`.

If you are unsure whether an idea fits, open an issue before writing a large patch.

## License

MIT. See `LICENSE`. Please read `SECURITY.md` before filing a vulnerability report. Agents should also read `AGENTS.md`.
