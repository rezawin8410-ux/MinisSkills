---
name: semantic-memory
description: Persistent cross-session knowledge base for Minis. Use when asked to remember facts, user preferences, project context, or recurring patterns — and when recalling what was learned in previous sessions. Complements the daily log with structured, searchable, typed memories.
version: 2.0.0
---

# Semantic Memory

A typed key/value store on top of `/var/minis/memory/` that survives session
resets (unlike `workspace/`) and is cheaper to search than raw daily logs.

## Storage layout

```
/var/minis/memory/
  GLOBAL.md            # read-only, user-maintained (settings, conventions)
  YYYY-MM-DD.md        # daily log — events, findings, action items
  semantic/            # <- THIS SKILL
    preferences.json   # user likes/dislikes, tool choices, formats
    projects.json      # project context: goals, stacks, decisions
    facts.json         # stable facts about the world, devices, accounts
    corrections.json   # things the agent keeps getting wrong + the fix
    routines.json      # recurring tasks and their proven commands
```

Each entry: `{"key": "...", "value": "...", "tags": [...], "updated": "ISO8601"}`.

## When to use

- **Save:** user states a preference ("I like X", "never do Y"), a project
  decision is made, a hard-won fact is discovered (a workaround, a device
  quirk, an API gotcha), or the same correction is given twice.
- **Recall:** at the start of any task, search by keyword before asking the
  user something that may already be known.
- **Do NOT save:** passwords, API keys, tokens, or anything the user marks
  confidential. Warn first; only persist if the user confirms.

## Commands (via the memory tool)

```
python3 tools/memory.py save <category> <key> <value> [tags]
python3 tools/memory.py recall <category> [query]
python3 tools/memory.py search <query>
python3 tools/memory.py list
python3 tools/memory.py delete <category> <key>
```

Categories map to the files above: `preferences`, `projects`, `facts`,
`corrections`, `routines`, plus free-form ones the user asks for.

## Rules

1. Write concise, factual entries — no narration, no "today I learned".
2. One fact per entry; a `key` is a stable identifier, not a sentence.
3. Prefer updating an existing key over creating a duplicate.
4. When a memory conflicts with the user's latest message, the user wins.
5. Cull entries that have been wrong twice — move them to `corrections.json`
   with the fix, so the mistake stops recurring.
