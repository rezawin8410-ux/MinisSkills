---
name: fact-gate
description: Before editing or creating a file, state the facts that justify the edit. Use for every file change.
---

# Fact gate

Before any `Write` or `Edit`, state in one short paragraph:

1. **Callers** — the files and lines that import, call or read this file (search for them; do not guess).
2. **Schemas** — the data this file reads or writes: formats, units, date conventions, table names.
3. **Purpose check** — that no existing file already does this (name what you searched).
4. **Instruction** — the user's words that this change answers, verbatim.

If any of the four cannot be established, say so and ask one precise question instead of editing.

The gate, as one block to fill before the edit:

```text
Callers: orchestra/cli.py:188, orchestra/daily.py:117
Schemas: reads seed/portfolio.json (bets[].name, launch_day_offset), writes nothing
Purpose check: searched "judge" in orchestra/ — no existing module does this
Instruction: "on va creer un reviewer judge independant"
```
