---
name: self-audit
description: End every task with an explicit audit of what was verified versus assumed. Use when closing any multi-step task.
---

# Self-audit

Close the task with three labelled lines:

- **Verified** — statements backed by something you ran or read, with the reference.
- **Assumed** — statements you relied on without checking, and why.
- **Not checked** — known gaps, each with the command or action that would check it.

Then scan your own changes for silent failures: swallowed exceptions, empty `except`, default values that hide missing data, retries without limits. Report each one found, fixed or left, with its line.

The closing block:

```text
Verified: 61 tests pass (python -m pytest -q, 22:22); registry shows toskill@0.1.0
Assumed: GitHub traffic API unchanged since last collect
Not checked: Windows arm64 binary — run bin/nobluff-windows-arm64.exe once
```
