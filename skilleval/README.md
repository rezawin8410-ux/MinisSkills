# skilleval

A tiny A/B eval harness for agent skills. Run the same cases with and
without a skill (or with two skills) and score them with deterministic
checks: `contains`, `not_contains`, `regex`, `exit_code`, `file_exists`,
`file_contains`.

No model API key is required. You bring a solver command — a stub script
in tests, or your own agent CLI later. An optional LLM judge is not wired
in this first release on purpose, so CI stays hermetic.

## Install

Python 3.10 or later.

From a checkout:

```bash
python3 -m pip install .
python3 -m pip install -e ".[dev]"  # optional, for tests
skilleval eval.json --solver 'python3 solver.py'
```

## 30-second quick start

```bash
mkdir -p release-notes
cat > release-notes/SKILL.md << 'SKILL'
---
name: release-notes
description: Writes release notes. Use when asked for a changelog.
---

Write release notes as a markdown heading plus bullets.
SKILL

cat > solver.py << 'SOLVER'
import os
import pathlib
prompt = os.environ["SKILLEVAL_PROMPT"]
skill = os.environ.get("SKILLEVAL_SKILL") or ""
out = pathlib.Path(os.environ["SKILLEVAL_OUT"])
text = "prompt:" + prompt + "\n"
if skill and (pathlib.Path(skill) / "SKILL.md").is_file():
    text += "# Release notes\n\n- fix timeout\n"
else:
    text += "lorem ipsum draft\n"
out.write_text(text)
SOLVER

cat > eval.json << 'EVAL'
{
  "solver": "python3 solver.py",
  "cases": [
    {
      "id": "changelog",
      "prompt": "Write release notes for: fix timeout",
      "skill": "./release-notes",
      "checks": [
        {"type": "contains", "text": "Release notes"},
        {"type": "contains", "text": "timeout"},
        {"type": "not_contains", "text": "lorem ipsum"},
        {"type": "regex", "pattern": "^# "},
        {"type": "exit_code", "value": 0}
      ]
    }
  ]
}
EVAL

skilleval eval.json
```

Sample output from that exact example:

```
$ skilleval eval.json
variant	checks passed	cases
baseline	2/5	1
with_skill	5/5	1

[changelog]
  baseline: 2/5
    FAIL  contains "Release notes"
    ok  contains "timeout"
    FAIL  not_contains "lorem ipsum"
    FAIL  regex ^# 
    ok  exit_code 0
  with_skill: 5/5
    ok  contains "Release notes"
    ok  contains "timeout"
    ok  not_contains "lorem ipsum"
    ok  regex ^# 
    ok  exit_code 0
exit: 0
```

The solver is run twice per case: once with `SKILLEVAL_SKILL` empty
(baseline) and once pointing at the skill folder. The report shows how
many checks each variant passed.

## Credit

Motivated by public A/B tests of skills with and without a file, and by
coding-agent evals that showed where a rule lives changes follow-through.
Original harness; no source was copied from any eval framework.

## Limits

- No model API key is required. You bring a solver command — a stub script in tests, or your own agent CLI later.
- An optional LLM judge is not wired in this release, so CI stays hermetic.
- The solver runs as a local subprocess with your user permissions in a temporary working directory. Treat it like any other script you run.
- `--timeout` applies to each solver invocation and must be a positive integer.

From GitHub you can also install with:

```bash
python3 -m pip install git+https://github.com/perron/skilleval.git
```

This package is not published to PyPI.

## Use it with your agent

You can point a coding agent at this repository and ask it to use the tool, adapt it, or write a small tool in the same style. That works with Claude Code, Codex, Cursor, Grok Bot, and anything else that can clone a git repo and run a Python CLI.

A short prompt you can paste:

> Clone https://github.com/perron/skilleval, install it, and run ./eval.json with the bundled stub solver. Explain the baseline vs with_skill table. Do not call a model API; this harness is deterministic and has no LLM judge in this release.

If you want a new tool in this style, say so explicitly: one job, standard-library Python, pytest first, no API key, and an honest Limits section.

## Contributing / how you can help

Bug reports, tight tests, and small honest features are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to run the suite and for pull request guidelines.

Good first issues:

- Allow a per-case `timeout` in the eval file.
- Add a junit-xml or markdown report format for CI logs.
- Support a `files` fixture that copies a binary fixture without going through UTF-8 text.

If you are unsure whether an idea fits, open an issue before writing a large patch.

## License

MIT. See `LICENSE`. Please read `SECURITY.md` before filing a vulnerability report. Agents should also read `AGENTS.md`.
